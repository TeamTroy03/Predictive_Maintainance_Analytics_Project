"""Typed tool for relationship questions over the knowledge graph derived from Gold (`gold_kg_nodes`, `gold_kg_edges`,
already built in Databricks, this code only reads them): which failure modes an asset or a type has, repeat-failure
chains, what tends to fail after what. The LLM only picks one of the TEMPLATES and its arguments; it never writes Cypher.

Two backends answer the same templates with the same result shape: Neo4j (filled from the Gold tables by
`graph_sync.py`) and, as a fallback, an in-process NetworkX graph built from the same Gold tables. Like the conversation
memory, the fallback is automatic: no NEO4J_URI, or Neo4j failing at call time, and the question is answered from memory.
Every result says which backend answered. Numbers are not recomputed here: they are the counts and sums stored on the
Gold graph edges; periods and KPIs stay with `get_kpi`."""

import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import Literal

import networkx as nx
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agent.tools.detail_tools import run_rows

logger = logging.getLogger("app.agent")  # same log file as agent.py (configured there)

MAX_LIMIT = 50
CACHE_SECONDS = int(os.getenv("GRAPH_CACHE_SECONDS", "3600"))  # how long the in-memory graph is reused
NEO4J_TIMEOUT_SECONDS = float(os.getenv("NEO4J_TIMEOUT_SECONDS", "5"))
NEO4J_COOLDOWN_SECONDS = 60  # after a Neo4j failure, answer from memory for this long before trying Neo4j again

_RECENT = "Only failures in Gold (unplanned downtime with a failure mode) are counted; planned work has no failure mode."
_NO_PERIOD = "No period filter: counts cover all data in Gold. Use get_kpi for a period."
_SEQUENCE = ("Follows means the next unplanned failure of the same equipment, in any mode; "
             "it shows a sequence, not a proven cause.")

# template -> arguments, what it returns and its limits; the Cypher and the in-memory code below follow the same specs
TEMPLATES = {
    "equipment_failure_modes": {
        "requires": ("equipment_id",), "optional": (), "source": "EXPERIENCED_FAILURE",
        "returns": "failure modes this equipment experienced: failures, downtime_hours, repeat_failures, last_failure_ts",
        "limitations": [_RECENT, _NO_PERIOD]},
    "type_failure_profile": {
        "requires": ("equipment_type",), "optional": (), "source": "TYPE_PRONE_TO",
        "returns": "failure modes of one equipment type: failures, equipment_affected, downtime_hours",
        "limitations": [_RECENT, _NO_PERIOD]},
    "repeat_failure_chain": {
        "requires": (), "optional": ("equipment_id",), "source": "REPEATED_FAILURE",
        "returns": "repeat failures of one equipment, or of the whole fleet when no equipment_id is given, newest first: "
                   "each work order with its equipment_id, the earlier work order it repeats, its failure mode and gap_days",
        "limitations": ["A repeat failure is the same equipment and failure mode again within 30 days (Gold rule)."]},
    "failures_after": {
        "requires": ("failure_mode",), "optional": ("equipment_type",), "source": "FOLLOWED_BY",
        "returns": "which failure modes follow a given one on the same equipment: occurrences and avg_gap_days; "
                   "optionally only for one equipment type",
        "limitations": [_RECENT, _SEQUENCE]},
}
TemplateName = Literal[*TEMPLATES]

# request field -> node type it names
ENTITY_FIELDS = {"equipment_id": "Equipment", "equipment_type": "EquipmentType", "failure_mode": "FailureMode"}
_NAME = r"^[A-Za-z0-9][A-Za-z0-9 _/().-]{0,63}$"


class GraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template: TemplateName
    equipment_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    equipment_type: str | None = Field(None, pattern=_NAME)
    failure_mode: str | None = Field(None, pattern=_NAME)  # code (BRG-WEAR) or description (Bearing wear)
    limit: int = Field(20, ge=1, le=MAX_LIMIT)

    @field_validator("equipment_id", "equipment_type", "failure_mode", mode="before")
    @classmethod
    def _blank_means_not_given(cls, value):
        """The LLM often sends "" instead of null for an argument it leaves out."""
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _arguments_fit_the_template(self):
        spec = TEMPLATES[self.template]
        allowed = {*spec["requires"], *spec["optional"]}
        if missing := [f for f in spec["requires"] if getattr(self, f) is None]:
            raise ValueError(f"{self.template} needs {', '.join(missing)}")
        if unused := [f for f in ENTITY_FIELDS if f not in allowed and getattr(self, f) is not None]:
            raise ValueError(f"{self.template} does not use {', '.join(unused)}")
        return self


def flatten_properties(text: str | None) -> dict:
    """The JSON `properties` column of the Gold graph tables as plain scalars (what Neo4j can store as properties)."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))} if isinstance(data, dict) else {}


def graph_prefix() -> str:
    """Table prefix of the graph tables: GRAPH_TABLE_PREFIX, else GOLD_TABLE_PREFIX (as for every Gold table). A separate
    variable lets the graph tables (e.g. `test_gold_kg_*`) differ from the prefix of the KPI tables."""
    return os.environ["GRAPH_TABLE_PREFIX"] if "GRAPH_TABLE_PREFIX" in os.environ else os.getenv("GOLD_TABLE_PREFIX", "")


def _graph_table(name: str) -> str:
    """Full name of `gold_kg_nodes` / `gold_kg_edges`, e.g. data2026acc123456.team3.test_gold_kg_nodes."""
    table = f"{os.environ['GOLD_CATALOG']}.{os.environ['GOLD_SCHEMA']}.{graph_prefix()}gold_{name}"
    if not re.fullmatch(r"[\w.]+", table):
        raise ValueError("GOLD_CATALOG, GOLD_SCHEMA and the table prefix may only contain letters, digits and _")
    return table


def gold_graph_ts() -> str | None:
    """gold_loaded_ts of the Gold graph tables (the later of the two), from one cheap aggregate query."""
    ts = run_rows(f"SELECT GREATEST((SELECT MAX(gold_loaded_ts) FROM {_graph_table('kg_nodes')}), "
                  f"(SELECT MAX(gold_loaded_ts) FROM {_graph_table('kg_edges')})) AS ts")[0]["ts"]
    return None if ts is None else str(ts)


def load_tables() -> tuple[list[dict], list[dict], str | None]:
    """(node rows, edge rows, gold_loaded_ts) from the two Gold graph tables on Databricks."""
    nodes = run_rows(f"SELECT node_id, node_type, business_key, label, properties FROM {_graph_table('kg_nodes')}")
    edges = run_rows(f"SELECT src_id, dst_id, edge_type, weight, properties FROM {_graph_table('kg_edges')}")
    return nodes, edges, gold_graph_ts()


def _clean(row: dict) -> dict:
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()}


class GraphStore:
    """Shared part of both backends: resolve the names in the request to nodes, run the template, build the result.
    A backend implements find(), rows() and loaded_ts()."""

    backend = ""

    def find(self, node_type: str, text: str) -> dict | None:
        """The node of this type whose business key or label equals `text` (case-insensitive):
        {"node_id", "business_key", "label"}, or None."""
        raise NotImplementedError

    def rows(self, template: str, found: dict[str, dict], limit: int) -> list[dict]:
        """Result rows of one template; `found` maps each request field to its node (see find)."""
        raise NotImplementedError

    def loaded_ts(self) -> str | None:
        """gold_loaded_ts of the Gold tables this graph was built from."""
        raise NotImplementedError

    def query(self, request: GraphRequest) -> dict:
        spec, loaded_ts = TEMPLATES[request.template], self.loaded_ts()
        found, missing = {}, []
        for field, node_type in ENTITY_FIELDS.items():
            if (value := getattr(request, field)) is not None:
                node = self.find(node_type, value)
                if node:
                    found[field] = node
                else:
                    missing.append(f"{field} '{value}'")
        rows = [] if missing else [_clean(r) for r in self.rows(request.template, found, request.limit)]
        limitations = list(spec["limitations"])
        if request.template == "repeat_failure_chain" and request.equipment_id is None:
            limitations.append("Whole fleet: the most recent repeat failures are listed, not a ranking of equipment. "
                               "Use equipment_failure_modes for the repeat counts of one equipment.")
        if missing:
            limitations.append(f"Not found in the graph: {', '.join(missing)}. Check the spelling.")
        elif not rows:
            limitations.append("No matching relationships in Gold for this selection.")
        if len(rows) == request.limit:
            limitations.append(f"The result is capped at {request.limit} rows.")
        label = f"{graph_prefix()}gold_kg_edges"
        return {
            "template": request.template,
            "filters": {field: node["business_key"] for field, node in found.items()},
            "rows": rows,
            "row_count": len(rows),
            "backend": self.backend,
            "source_measure": f"{label}: {spec['source']} edges ({spec['returns']})",
            "gold_loaded_ts": loaded_ts,
            "limitations": limitations,
        }


# ---------- In-memory backend (fallback) ----------

class MemoryStore(GraphStore):
    """NetworkX graph built from the Gold tables, reused for CACHE_SECONDS. `loader` returns what load_tables() does."""

    backend = "memory"

    def __init__(self, loader=load_tables, cache_seconds: int = CACHE_SECONDS):
        self._loader, self._cache_seconds = loader, cache_seconds
        self._built_at: float | None = None

    def _graph(self) -> nx.MultiDiGraph:
        if self._built_at is None or time.monotonic() - self._built_at > self._cache_seconds:
            nodes, edges, ts = self._loader()
            graph, by_type, names = nx.MultiDiGraph(), defaultdict(list), {}
            for n in nodes:
                graph.add_node(n["node_id"], node_type=n["node_type"], business_key=n["business_key"], label=n["label"],
                               **flatten_properties(n["properties"]))
                for text in (n["business_key"], n["label"]):  # business key wins over a label that looks the same
                    names.setdefault((n["node_type"], str(text).lower()), n["node_id"])
            for e in edges:
                attrs = {**flatten_properties(e["properties"]), "weight": float(e["weight"])}
                graph.add_edge(e["src_id"], e["dst_id"], key=e["edge_type"], **attrs)
                by_type[e["edge_type"]].append((e["src_id"], e["dst_id"], attrs))
            self._built, self._by_type, self._names, self._ts = graph, by_type, names, ts
            self._built_at = time.monotonic()
        return self._built

    def loaded_ts(self) -> str | None:
        self._graph()
        return self._ts

    def find(self, node_type, text):
        graph = self._graph()
        node_id = self._names.get((node_type, text.lower()))
        if node_id is None:
            return None
        return {"node_id": node_id, "business_key": graph.nodes[node_id]["business_key"],
                "label": graph.nodes[node_id]["label"]}

    def _out(self, node_id: str, edge_type: str):
        """(target node id, edge attributes) of the edges of one type leaving a node."""
        return [(v, d) for _, v, k, d in self._built.out_edges(node_id, keys=True, data=True) if k == edge_type]

    def rows(self, template, found, limit):
        g = self._graph()
        if template in ("equipment_failure_modes", "type_failure_profile"):
            owner, edge_type = (("equipment_id", "EXPERIENCED_FAILURE") if template == "equipment_failure_modes"
                                else ("equipment_type", "TYPE_PRONE_TO"))
            rows = []
            for mode, d in self._out(found[owner]["node_id"], edge_type):
                row = {"failure_mode": g.nodes[mode]["business_key"], "description": g.nodes[mode]["label"],
                       "failures": int(d["weight"])}
                if template == "equipment_failure_modes":
                    row |= {"downtime_hours": d.get("downtime_hours"), "repeat_failures": d.get("repeat_failures"),
                            "last_failure_ts": d.get("last_failure_ts")}
                else:
                    row |= {"equipment_affected": d.get("equipment_affected"), "downtime_hours": d.get("downtime_hours")}
                rows.append(row)
            rows.sort(key=lambda r: (-r["failures"], r["failure_mode"]))
        elif template == "repeat_failure_chain":
            wanted = found["equipment_id"]["node_id"] if "equipment_id" in found else None  # None = whole fleet
            rows = []
            for work_order, earlier, d in self._by_type["REPEATED_FAILURE"]:
                equipment = next((e for e, _ in self._out(work_order, "PERFORMED_ON")), None)
                if equipment is None or (wanted and equipment != wanted):
                    continue
                rows.append({"work_order": g.nodes[work_order]["business_key"],
                             "equipment_id": g.nodes[equipment]["business_key"],
                             "repeats_work_order": g.nodes[earlier]["business_key"],
                             "failure_mode": d.get("failure_mode"), "gap_days": d["weight"],
                             "created_ts": g.nodes[work_order].get("created_ts")})
            rows.sort(key=lambda r: r["work_order"])  # newest first, ties by work order (same as the Cypher ORDER BY)
            rows.sort(key=lambda r: r["created_ts"] or "", reverse=True)
        else:  # failures_after
            mode = found["failure_mode"]["business_key"]
            type_id = found["equipment_type"]["node_id"] if "equipment_type" in found else None
            count, gap = defaultdict(int), defaultdict(float)
            for first, _, d in self._by_type["FOLLOWED_BY"]:
                if d.get("from_mode") != mode:
                    continue
                if type_id and not any(g.has_edge(equipment, type_id, "IS_OF_TYPE")
                                       for equipment, _ in self._out(first, "PERFORMED_ON")):
                    continue
                count[d["to_mode"]] += 1
                gap[d["to_mode"]] += d["weight"]
            rows = sorted(({"next_failure_mode": m, "occurrences": n, "avg_gap_days": gap[m] / n}
                           for m, n in count.items()), key=lambda r: (-r["occurrences"], r["next_failure_mode"]))
        return rows[:limit]


# ---------- Neo4j backend ----------

FIND_CYPHER = ("MATCH (n:Node {node_type: $type}) WHERE toLower(n.business_key) = toLower($text) "
               "OR toLower(n.label) = toLower($text) "
               "RETURN n.node_id AS node_id, n.business_key AS business_key, n.label AS label "
               "ORDER BY n.business_key = $text DESC LIMIT 1")
META_CYPHER = "MATCH (m:GraphMeta {id: 'kg'}) RETURN m.gold_loaded_ts AS ts"
# One fixed, parameterised query per template; values are only ever bound parameters.
CYPHER = {
    "equipment_failure_modes": (
        "MATCH (:Node {node_id: $equipment})-[r:EXPERIENCED_FAILURE]->(f:Node) "
        "RETURN f.business_key AS failure_mode, f.label AS description, toInteger(r.weight) AS failures, "
        "r.downtime_hours AS downtime_hours, r.repeat_failures AS repeat_failures, r.last_failure_ts AS last_failure_ts "
        "ORDER BY failures DESC, failure_mode LIMIT $limit"),
    "type_failure_profile": (
        "MATCH (:Node {node_id: $type})-[r:TYPE_PRONE_TO]->(f:Node) "
        "RETURN f.business_key AS failure_mode, f.label AS description, toInteger(r.weight) AS failures, "
        "r.equipment_affected AS equipment_affected, r.downtime_hours AS downtime_hours "
        "ORDER BY failures DESC, failure_mode LIMIT $limit"),
    "repeat_failure_chain": (
        "MATCH (w:Node)-[:PERFORMED_ON]->(e:Node) WHERE $equipment IS NULL OR e.node_id = $equipment "
        "MATCH (w)-[r:REPEATED_FAILURE]->(p:Node) "
        "RETURN w.business_key AS work_order, e.business_key AS equipment_id, p.business_key AS repeats_work_order, "
        "r.failure_mode AS failure_mode, r.weight AS gap_days, w.created_ts AS created_ts "
        "ORDER BY coalesce(created_ts, '') DESC, work_order LIMIT $limit"),
    "failures_after": (
        "MATCH (a:Node)-[f:FOLLOWED_BY]->(:Node) WHERE f.from_mode = $mode "
        "AND ($type IS NULL OR EXISTS { (a)-[:PERFORMED_ON]->(:Node)-[:IS_OF_TYPE]->(:Node {node_id: $type}) }) "
        "RETURN f.to_mode AS next_failure_mode, count(*) AS occurrences, avg(f.weight) AS avg_gap_days "
        "ORDER BY occurrences DESC, next_failure_mode LIMIT $limit"),
}


class Neo4jStore(GraphStore):
    """`run(query, params) -> list of row dicts` is the only thing that touches the driver (see cypher_runner)."""

    backend = "neo4j"

    def __init__(self, run):
        self._run = run

    def loaded_ts(self):
        rows = self._run(META_CYPHER, {})
        if not rows:  # empty database: let the caller fall back instead of answering "nothing found"
            raise RuntimeError("the knowledge graph is not loaded in Neo4j (run app.agent.tools.graph_sync)")
        return rows[0]["ts"]

    def find(self, node_type, text):
        rows = self._run(FIND_CYPHER, {"type": node_type, "text": text})
        return rows[0] if rows else None

    def rows(self, template, found, limit):
        def node_id(field):
            return found[field]["node_id"] if field in found else None

        params = {
            "equipment_failure_modes": {"equipment": node_id("equipment_id")},
            "type_failure_profile": {"type": node_id("equipment_type")},
            "repeat_failure_chain": {"equipment": node_id("equipment_id")},
            "failures_after": {"mode": found.get("failure_mode", {}).get("business_key"),
                               "type": node_id("equipment_type")},
        }[template]
        return self._run(CYPHER[template], {**params, "limit": limit})


def cypher_runner(driver, database: str, timeout: float = NEO4J_TIMEOUT_SECONDS):
    """Read-only runner for Neo4jStore: one read session per query, bounded by a server-side timeout."""
    from neo4j import READ_ACCESS, Query

    def run(query: str, params: dict) -> list[dict]:
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            return [record.data() for record in session.run(Query(query, timeout=timeout), params)]

    return run


# ---------- Backend choice ----------

class GraphService:
    """Neo4j when it is configured and answering, else the in-process graph. A Neo4j error is logged, the question is
    answered from memory, and Neo4j is not tried again for NEO4J_COOLDOWN_SECONDS (so a down server costs one timeout)."""

    def __init__(self, neo4j: Neo4jStore | None, memory: MemoryStore, cooldown_seconds: float = NEO4J_COOLDOWN_SECONDS):
        self.neo4j, self.memory, self._cooldown, self._retry_at = neo4j, memory, cooldown_seconds, 0.0

    def query(self, request: GraphRequest) -> dict:
        if self.neo4j and time.monotonic() >= self._retry_at:
            try:
                return self.neo4j.query(request)
            except Exception as e:  # BLE001: server down, bad credentials, graph not loaded...
                self._retry_at = time.monotonic() + self._cooldown
                logger.warning("graph_neo4j_unavailable", extra={"fields": {"error": type(e).__name__}})
        result = self.memory.query(request)  # raises when Databricks is down too: the agent reports the tool failure
        if self.neo4j:
            result["limitations"].append("Neo4j was not available: answered from the in-memory graph built from the "
                                         "same Gold tables.")
        return result


def make_graph_service() -> GraphService:
    """Neo4j-backed when NEO4J_URI is set (NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE default `neo4j`), else memory only.
    The driver connects lazily, so a server that is down at startup is only noticed (and then bypassed) on the first
    question and is retried later."""
    uri, neo4j = os.environ.get("NEO4J_URI"), None
    if uri:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(uri, auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
                                          connection_timeout=2, connection_acquisition_timeout=2)
            neo4j = Neo4jStore(cypher_runner(driver, os.getenv("NEO4J_DATABASE", "neo4j")))
        except Exception as e:  # BLE001: bad URI, driver missing
            logger.warning("graph_neo4j_unavailable", extra={"fields": {"error": type(e).__name__}})
    return GraphService(neo4j, MemoryStore())


_service: GraphService | None = None


def get_service() -> GraphService:
    global _service
    if _service is None:  # lazy: importing the agent must not open connections
        _service = make_graph_service()
    return _service


@tool
def query_graph(template: TemplateName, equipment_id: str | None = None, equipment_type: str | None = None,
                failure_mode: str | None = None, limit: int = 20) -> dict:
    """Answer relationship questions from the knowledge graph of the Gold layer (never guess them yourself).
    Pick one template: equipment_failure_modes (needs equipment_id): failure modes this equipment experienced, with
    failures, downtime hours and repeat failures; type_failure_profile (needs equipment_type): failure modes of one
    equipment type; repeat_failure_chain (equipment_id optional): repeat failures and the earlier work orders they
    repeat, of one equipment or, without equipment_id, of the whole fleet, newest first (use it for "which equipment
    have repeat failures": leave equipment_id out); failures_after (needs failure_mode, optional equipment_type): which failure modes follow the given one on
    the same equipment, with occurrences and average gap in days. failure_mode is a code (BRG-WEAR) or its description
    (Bearing wear). limit is 1-50 rows (default 20). No period filter: use get_kpi for KPIs over months. The result has
    rows, filters, backend, source_measure, gold_loaded_ts and limitations."""
    try:
        request = GraphRequest(template=template, equipment_id=equipment_id, equipment_type=equipment_type,
                               failure_mode=failure_mode, limit=limit)
    except ValidationError as e:
        return {"error": f"Invalid graph request: {e.errors(include_url=False, include_input=False)}"}
    return get_service().query(request)
