"""Contract test: the Neo4j backend and the in-memory backend of `query_graph` must give the same answer to every
template. The tiny fixture graph of tests/unit/graph_fixture.py is loaded into Neo4j with graph_sync and both backends
are queried. This guards the one real cost of having two implementations of each template.

Needs a THROWAWAY Neo4j: the sync replaces everything in the database. Point NEO4J_TEST_URI (and NEO4J_TEST_USER,
NEO4J_TEST_PASSWORD, NEO4J_TEST_DATABASE) at it, for example
    docker run -d --name pm-neo4j-test -p 127.0.0.1:7687:7687 -e NEO4J_AUTH=neo4j/<pw> neo4j:5
It is deliberately NOT read from NEO4J_URI, and it refuses a database that already holds another graph.
Run:  python -m pytest tests/integration/test_graph_neo4j.py -m integration -v"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))  # the shared fixture lives next to the unit tests
from graph_fixture import EDGES, NODES, TS, load_fixture

from app.agent.tools import graph_sync
from app.agent.tools.graph_tools import GraphRequest, MemoryStore, Neo4jStore, cypher_runner

pytestmark = pytest.mark.integration

URI = os.environ.get("NEO4J_TEST_URI")
DATABASE = os.environ.get("NEO4J_TEST_DATABASE", "neo4j")

REQUESTS = [
    {"template": "equipment_failure_modes", "equipment_id": "EQ-1"},
    {"template": "equipment_failure_modes", "equipment_id": "eq-3"},
    {"template": "equipment_failure_modes", "equipment_id": "EQ-999"},
    {"template": "type_failure_profile", "equipment_type": "Pump"},
    {"template": "type_failure_profile", "equipment_type": "Pump", "limit": 1},
    {"template": "repeat_failure_chain", "equipment_id": "EQ-1"},
    {"template": "repeat_failure_chain", "equipment_id": "EQ-2"},
    {"template": "repeat_failure_chain"},
    {"template": "failures_after", "failure_mode": "BRG-WEAR"},
    {"template": "failures_after", "failure_mode": "Bearing wear", "equipment_type": "Pump"},
    {"template": "failures_after", "failure_mode": "Bearing wear", "equipment_type": "Motor"},
]


@pytest.fixture(scope="module")
def stores():
    if not URI:
        pytest.skip("NEO4J_TEST_URI is not set (needs a throwaway Neo4j, see the module docstring)")
    from neo4j import GraphDatabase

    auth = (os.getenv("NEO4J_TEST_USER", "neo4j"), os.getenv("NEO4J_TEST_PASSWORD", ""))
    with GraphDatabase.driver(URI, auth=auth) as driver:
        driver.verify_connectivity()
        with driver.session(database=DATABASE) as session:
            others = session.run("MATCH (n:Node) WHERE NOT n.node_id IN $ids RETURN count(n) AS n",
                                 ids=[n["node_id"] for n in NODES]).single()["n"]
        if others:
            pytest.skip(f"{DATABASE} already holds {others} other graph nodes: refusing to overwrite it")
        graph_sync.sync(driver, NODES, EDGES, TS, DATABASE)
        yield Neo4jStore(cypher_runner(driver, DATABASE)), MemoryStore(loader=load_fixture)


@pytest.mark.parametrize("args", REQUESTS, ids=lambda a: f"{a['template']}-{a.get('equipment_id') or a.get('equipment_type') or a.get('failure_mode')}")
def test_neo4j_and_memory_answer_the_same(stores, args):
    neo4j, memory = stores
    request = GraphRequest(**args)
    a, b = neo4j.query(request), memory.query(request)
    assert (a["rows"], a["filters"], a["gold_loaded_ts"], a["limitations"]) == \
           (b["rows"], b["filters"], b["gold_loaded_ts"], b["limitations"])
    assert (a["backend"], b["backend"]) == ("neo4j", "memory")


def test_sync_is_idempotent(stores):
    from neo4j import GraphDatabase

    auth = (os.getenv("NEO4J_TEST_USER", "neo4j"), os.getenv("NEO4J_TEST_PASSWORD", ""))
    with GraphDatabase.driver(URI, auth=auth) as driver:
        graph_sync.sync(driver, NODES, EDGES, TS, DATABASE)
        graph_sync.sync(driver, NODES, EDGES, TS, DATABASE)
        with driver.session(database=DATABASE) as session:
            assert session.run("MATCH (n:Node) RETURN count(n) AS n").single()["n"] == len(NODES)
            assert session.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"] == len(EDGES)
