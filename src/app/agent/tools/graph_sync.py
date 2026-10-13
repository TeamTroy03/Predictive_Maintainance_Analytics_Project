"""Load the Gold knowledge graph tables (`gold_kg_nodes`, `gold_kg_edges`) into Neo4j, so `query_graph` can use it.
The two tables are already built in Databricks (this repo only reads them): Delta stays the source of truth, Neo4j is a copy:

    uv run python -m app.agent.tools.graph_sync              # needs NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD (+ Databricks)
    uv run python -m app.agent.tools.graph_sync --dry-run    # reads Gold and prints the counts, connects to no Neo4j

The API runs it by itself at startup (`start_background_sync`, see below), so this command is for a manual refresh.

Idempotent: nodes and edges are MERGEd by node_id / (source, target, type), everything written in a run carries the
run's sync_id, and what no longer exists in Gold (other sync_id) is deleted at the end, so a rerun never duplicates and
a shrunk Gold leaves no stale data. Every node has the label Node plus its type (Equipment, WorkOrder, ...); an edge's
relationship type is its edge_type. The JSON `properties` of the tables become plain properties. One (:GraphMeta) node
keeps the gold_loaded_ts the copy was made from."""

import argparse
import logging
import os
import sys
import threading
import uuid

from app.agent.tools.graph_tools import META_CYPHER as READ_META
from app.agent.tools.graph_tools import flatten_properties, gold_graph_ts, load_tables

logger = logging.getLogger("app.graph_sync")  # the API's JSON log (root logger)

BATCH = 1000
# Labels and relationship types cannot be query parameters, so they are checked against these lists before they are
# written into a statement. A new type in Gold has to be added here on purpose.
NODE_TYPES = ("Site", "EquipmentType", "Equipment", "Sensor", "FailureMode", "MaintenanceType", "WorkOrder")
EDGE_TYPES = ("LOCATED_AT", "IS_OF_TYPE", "HAS_SENSOR", "PERFORMED_ON", "HAS_FAILURE_MODE", "HAS_MAINTENANCE_TYPE",
              "REPEATED_FAILURE", "FOLLOWED_BY", "EXPERIENCED_FAILURE", "TYPE_PRONE_TO")

SCHEMA = (
    "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE",
    "CREATE INDEX node_type_key IF NOT EXISTS FOR (n:Node) ON (n.node_type, n.business_key)",
)
NODE_CYPHER = ("UNWIND $rows AS row MERGE (n:Node {{node_id: row.node_id}}) SET n:{label} SET n += row.props, "
               "n.node_type = row.node_type, n.business_key = row.business_key, n.label = row.label, n.sync_id = $sync")
EDGE_CYPHER = ("UNWIND $rows AS row MATCH (a:Node {{node_id: row.src}}) MATCH (b:Node {{node_id: row.dst}}) "
               "MERGE (a)-[r:{type}]->(b) SET r += row.props, r.weight = row.weight, r.sync_id = $sync")
CLEANUP = ("MATCH ()-[r]->() WHERE r.sync_id IS NOT NULL AND r.sync_id <> $sync DELETE r",
           "MATCH (n:Node) WHERE n.sync_id <> $sync DETACH DELETE n")
META_CYPHER = ("MERGE (m:GraphMeta {id: 'kg'}) SET m.gold_loaded_ts = $ts, m.synced_at = datetime(), "
               "m.nodes = $nodes, m.edges = $edges, m.sync_id = $sync")


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _group(rows: list[dict], key: str, allowed: tuple[str, ...], what: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    if unknown := sorted(set(groups) - set(allowed)):
        raise ValueError(f"unknown {what} in the Gold graph tables: {', '.join(unknown)} (add it to graph_sync.py)")
    return groups


def sync(driver, nodes: list[dict], edges: list[dict], gold_loaded_ts: str | None, database: str = "neo4j",
         batch: int = BATCH) -> dict:
    """Copy the rows into Neo4j (see the module docstring). `driver` is a neo4j driver; returns the written counts."""
    by_node_type = _group(nodes, "node_type", NODE_TYPES, "node type")
    by_edge_type = _group(edges, "edge_type", EDGE_TYPES, "edge type")
    sync_id = uuid.uuid4().hex
    with driver.session(database=database) as session:
        for statement in SCHEMA:
            session.run(statement).consume()
        for node_type, rows in by_node_type.items():
            for part in _batches(rows, batch):
                payload = [{"node_id": n["node_id"], "node_type": n["node_type"], "business_key": n["business_key"],
                            "label": n["label"], "props": flatten_properties(n["properties"])} for n in part]
                session.run(NODE_CYPHER.format(label=node_type), rows=payload, sync=sync_id).consume()
        for edge_type, rows in by_edge_type.items():
            for part in _batches(rows, batch):
                payload = [{"src": e["src_id"], "dst": e["dst_id"], "weight": float(e["weight"]),
                            "props": flatten_properties(e["properties"])} for e in part]
                session.run(EDGE_CYPHER.format(type=edge_type), rows=payload, sync=sync_id).consume()
        for statement in CLEANUP:
            session.run(statement, sync=sync_id).consume()
        session.run(META_CYPHER, ts=gold_loaded_ts, nodes=len(nodes), edges=len(edges), sync=sync_id).consume()
    return {"nodes": len(nodes), "edges": len(edges), "gold_loaded_ts": gold_loaded_ts, "sync_id": sync_id}


def _auth() -> tuple[str, str]:
    return os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")


def startup_sync(driver=None, read_gold_ts=gold_graph_ts, load=load_tables) -> str:
    """Bring Neo4j up to date with the Gold graph tables, but only when Gold has a newer load than the copy
    (compares gold_loaded_ts, so a restart without a new Gold run costs two small queries, not a sync). Returns what it
    did. Needs NEO4J_URI; GRAPH_SYNC_ON_STARTUP=false switches it off. `driver`, `read_gold_ts` and `load` are replaceable for tests."""
    if os.getenv("GRAPH_SYNC_ON_STARTUP", "true").strip().lower() in ("0", "false", "no", "off"):
        return "skipped: GRAPH_SYNC_ON_STARTUP is off"
    own_driver = driver is None
    if own_driver:
        if not (uri := os.environ.get("NEO4J_URI")):
            return "skipped: NEO4J_URI is not set"
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=_auth())
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        gold_ts = read_gold_ts()
        if gold_ts is None:
            return "skipped: the Gold graph tables are empty"
        with driver.session(database=database) as session:
            record = session.run(READ_META).single()
        if record and record["ts"] == gold_ts:
            return f"up to date (gold_loaded_ts {gold_ts})"
        nodes, edges, ts = load()
        done = sync(driver, nodes, edges, ts, database)
        return f"synced {done['nodes']} nodes and {done['edges']} edges (gold_loaded_ts {ts})"
    finally:
        if own_driver:
            driver.close()


def start_background_sync() -> threading.Thread | None:
    """Run startup_sync in a daemon thread, called by the API at startup. Does nothing without NEO4J_URI. It never blocks
    the start and never fails it: until it has finished, `query_graph` answers from the in-memory graph (Neo4j has no
    data yet), and an error is only logged."""
    from dotenv import load_dotenv

    load_dotenv()  # the API does not load .env into the environment by itself, the agent tools read it from there
    if not os.environ.get("NEO4J_URI"):
        return None

    def work() -> None:
        logger.info("graph_sync_started")
        try:
            logger.info("graph_sync_finished", extra={"result": startup_sync()})
        except Exception as e:  # BLE001: Neo4j or Databricks down, a startup task must not crash the API
            logger.warning("graph_sync_failed", extra={"error": type(e).__name__})

    thread = threading.Thread(target=work, name="graph-sync", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the Gold knowledge graph tables into Neo4j.")
    parser.add_argument("--dry-run", action="store_true", help="read Gold and print the counts, do not connect to Neo4j")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    nodes, edges, ts = load_tables()
    print(f"Gold: {len(nodes)} nodes, {len(edges)} edges, gold_loaded_ts={ts}")
    _group(nodes, "node_type", NODE_TYPES, "node type")  # fail before any write, also in a dry run
    _group(edges, "edge_type", EDGE_TYPES, "edge type")
    if args.dry_run:
        return 0
    if not (uri := os.environ.get("NEO4J_URI")):
        print("NEO4J_URI is not set (use --dry-run to only read Gold).", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase

    with GraphDatabase.driver(uri, auth=_auth()) as driver:
        driver.verify_connectivity()
        done = sync(driver, nodes, edges, ts, os.getenv("NEO4J_DATABASE", "neo4j"))
    print(f"Neo4j: {done['nodes']} nodes and {done['edges']} edges synced (sync_id {done['sync_id']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
