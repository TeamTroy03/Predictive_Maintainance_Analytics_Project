"""Knowledge graph tool: request validation, the in-memory backend, the Neo4j backend with a fake runner, the fallback
between them and the sync into Neo4j (a fake driver). No Neo4j, Databricks or LLM needed."""

import pytest
from graph_fixture import EDGES, NODES, TS, load_fixture

from app.agent.tools import graph_sync, graph_tools
from app.agent.tools.graph_tools import (
    CYPHER,
    GraphRequest,
    GraphService,
    MemoryStore,
    Neo4jStore,
    flatten_properties,
    query_graph,
)


def request(template, **kwargs):
    return GraphRequest(template=template, **kwargs)


@pytest.fixture
def memory():
    return MemoryStore(loader=load_fixture)


# ---------- request validation ----------

@pytest.mark.parametrize("args", [
    {"template": "type_failure_profile"},                                          # needs equipment_type
    {"template": "equipment_failure_modes", "equipment_id": "EQ-1", "failure_mode": "BRG-WEAR"},  # not used by it
    {"template": "failures_after", "failure_mode": "BRG-WEAR", "equipment_id": "EQ-1"},
    {"template": "repeat_failure_chain", "equipment_type": "Pump"},                   # only equipment_id is optional there
    {"template": "type_failure_profile", "equipment_type": "Pump", "limit": 0},
    {"template": "type_failure_profile", "equipment_type": "Pump", "limit": 51},
    {"template": "type_failure_profile", "equipment_type": "Pump'}) MATCH (n) DETACH DELETE n //"},
    {"template": "equipment_failure_modes", "equipment_id": "EQ-1 OR 1=1"},
    {"template": "delete_everything", "equipment_id": "EQ-1"},
    {"template": "equipment_failure_modes", "equipment_id": "EQ-1", "cypher": "MATCH (n) RETURN n"},  # extra field
])
def test_invalid_requests_are_rejected(args):
    with pytest.raises(ValueError):
        GraphRequest(**args)


def test_valid_requests_and_defaults():
    assert request("failures_after", failure_mode="Bearing wear").limit == 20
    assert request("failures_after", failure_mode="BRG-WEAR", equipment_type="Pump").equipment_type == "Pump"


def test_graph_tables_follow_their_own_prefix_or_the_gold_prefix(monkeypatch):
    monkeypatch.setenv("GOLD_CATALOG", "cat")
    monkeypatch.setenv("GOLD_SCHEMA", "sch")
    monkeypatch.setenv("GOLD_TABLE_PREFIX", "")
    monkeypatch.delenv("GRAPH_TABLE_PREFIX", raising=False)
    assert graph_tools._graph_table("kg_nodes") == "cat.sch.gold_kg_nodes"
    monkeypatch.setenv("GRAPH_TABLE_PREFIX", "test_")
    assert graph_tools._graph_table("kg_edges") == "cat.sch.test_gold_kg_edges"
    monkeypatch.setenv("GRAPH_TABLE_PREFIX", "x; DROP TABLE y; --")
    with pytest.raises(ValueError):
        graph_tools._graph_table("kg_nodes")


def test_flatten_properties():
    assert flatten_properties('{"a": 1, "b": "x", "c": null, "d": {"n": 1}, "e": true}') == {"a": 1, "b": "x", "e": True}
    assert flatten_properties(None) == {} and flatten_properties("not json") == {} and flatten_properties("[1]") == {}


# ---------- in-memory backend ----------

def test_equipment_failure_modes(memory):
    result = memory.query(request("equipment_failure_modes", equipment_id="eq-1"))  # case-insensitive
    assert result["backend"] == "memory" and result["gold_loaded_ts"] == TS
    assert result["filters"] == {"equipment_id": "EQ-1"}
    assert result["rows"] == [
        {"equipment_id": "EQ-1", "failure_mode": "BRG-WEAR", "description": "Bearing wear", "failures": 2,
         "downtime_hours": 11.5, "repeat_failures": 1, "last_failure_ts": "2026-02-01T08:00:00.000Z"},
        {"equipment_id": "EQ-1", "failure_mode": "OVERHEAT", "description": "Overheating", "failures": 1,
         "downtime_hours": 3.0, "repeat_failures": 0, "last_failure_ts": "2026-03-01T08:00:00.000Z"},
    ]
    assert result["row_count"] == 2 and result["limitations"]


def test_type_failure_profile(memory):
    rows = memory.query(request("type_failure_profile", equipment_type="PUMP"))["rows"]
    assert [(r["failure_mode"], r["failures"], r["equipment_affected"]) for r in rows] == [
        ("BRG-WEAR", 3, 2), ("OVERHEAT", 2, 2)]


def test_repeat_failure_chain(memory):
    rows = memory.query(request("repeat_failure_chain", equipment_id="EQ-1"))["rows"]
    assert rows == [{"work_order": "WO-2", "equipment_id": "EQ-1", "repeats_work_order": "WO-1",
                     "failure_mode": "BRG-WEAR", "gap_days": 5.5, "created_ts": "2026-02-01T08:00:00.000Z"}]
    assert memory.query(request("repeat_failure_chain", equipment_id="EQ-2"))["rows"] == []


def test_repeat_failure_chain_without_equipment_covers_the_fleet_newest_first():
    later_repeat = {"src_id": "WorkOrder:WO-6", "dst_id": "WorkOrder:WO-4", "edge_type": "REPEATED_FAILURE",
                    "weight": 3.0, "properties": '{"failure_mode": "OVERHEAT"}'}  # on EQ-2, newer than WO-2 of EQ-1
    store = MemoryStore(loader=lambda: (NODES, [*EDGES, later_repeat], TS))
    result = store.query(request("repeat_failure_chain"))
    assert [(r["work_order"], r["equipment_id"]) for r in result["rows"]] == [("WO-6", "EQ-2"), ("WO-2", "EQ-1")]
    assert result["filters"] == {} and any("Whole fleet" in lim for lim in result["limitations"])
    one = store.query(request("repeat_failure_chain", equipment_id="eq-2"))
    assert [r["work_order"] for r in one["rows"]] == ["WO-6"] and not any("Whole fleet" in x for x in one["limitations"])


def test_equipment_failure_modes_without_equipment_covers_the_fleet_most_failures_first():
    other = {"src_id": "Equipment:EQ-2", "dst_id": "FailureMode:OVERHEAT", "edge_type": "EXPERIENCED_FAILURE",
             "weight": 5.0, "properties": '{"downtime_hours": 9.0, "repeat_failures": 2, "last_failure_ts": "2026-04-01"}'}
    store = MemoryStore(loader=lambda: (NODES, [*EDGES, other], TS))
    result = store.query(request("equipment_failure_modes"))
    assert [(r["equipment_id"], r["failure_mode"], r["failures"]) for r in result["rows"]] == [
        ("EQ-2", "OVERHEAT", 5), ("EQ-1", "BRG-WEAR", 2), ("EQ-1", "OVERHEAT", 1)]
    assert result["filters"] == {} and any("Whole fleet" in lim for lim in result["limitations"])
    one = store.query(request("equipment_failure_modes", equipment_id="EQ-2"))
    assert [r["equipment_id"] for r in one["rows"]] == ["EQ-2"] and not any("Whole fleet" in x for x in one["limitations"])


def test_blank_arguments_count_as_not_given(memory):
    assert request("repeat_failure_chain", equipment_id="  ").equipment_id is None
    assert request("failures_after", failure_mode="BRG-WEAR", equipment_type="").equipment_type is None
    with pytest.raises(ValueError):  # a required argument left blank is still missing
        request("type_failure_profile", equipment_type="")
    assert request("equipment_failure_modes", equipment_id="").equipment_id is None
    assert memory.query(request("repeat_failure_chain", equipment_id=""))["row_count"] == 1


def test_failures_after_fleet_and_by_type(memory):
    fleet = memory.query(request("failures_after", failure_mode="BRG-WEAR"))["rows"]
    assert fleet == [{"next_failure_mode": "OVERHEAT", "occurrences": 3, "avg_gap_days": 6.67},
                     {"next_failure_mode": "BRG-WEAR", "occurrences": 1, "avg_gap_days": 5.5}]
    pumps = memory.query(request("failures_after", failure_mode="Bearing wear", equipment_type="Pump"))["rows"]
    assert pumps == [{"next_failure_mode": "OVERHEAT", "occurrences": 2, "avg_gap_days": 6.0},
                     {"next_failure_mode": "BRG-WEAR", "occurrences": 1, "avg_gap_days": 5.5}]


def test_failure_mode_is_found_by_code_or_description(memory):
    by_code = memory.query(request("failures_after", failure_mode="brg-wear"))
    by_text = memory.query(request("failures_after", failure_mode="BEARING WEAR"))
    assert by_code["rows"] == by_text["rows"] and by_code["filters"] == {"failure_mode": "BRG-WEAR"}


def test_unknown_names_give_no_rows_and_a_limitation(memory):
    result = memory.query(request("equipment_failure_modes", equipment_id="EQ-999"))
    assert result["rows"] == [] and any("EQ-999" in lim for lim in result["limitations"])


def test_known_node_without_matches_says_so(memory):
    result = memory.query(request("equipment_failure_modes", equipment_id="EQ-3"))
    assert result["rows"] == [] and any("No matching relationships" in lim for lim in result["limitations"])


def test_limit_caps_rows_and_is_reported(memory):
    result = memory.query(request("type_failure_profile", equipment_type="Pump", limit=1))
    assert result["row_count"] == 1 and any("capped at 1" in lim for lim in result["limitations"])


def test_graph_is_cached_until_it_expires():
    loads = []

    def loader():
        loads.append(1)
        return load_fixture()

    cached = MemoryStore(loader=loader, cache_seconds=3600)
    cached.query(request("equipment_failure_modes", equipment_id="EQ-1"))
    cached.query(request("type_failure_profile", equipment_type="Pump"))
    assert len(loads) == 1
    expired = MemoryStore(loader=loader, cache_seconds=-1)  # always older than the cache time
    expired.query(request("equipment_failure_modes", equipment_id="EQ-1"))
    assert len(loads) > 1


# ---------- Neo4j backend (fake runner) ----------

class FakeRunner:
    """Stands in for cypher_runner(): records (query, params) and answers from a dict."""

    def __init__(self, meta=({"ts": TS},), nodes=None, rows=()):
        self.calls, self.meta, self.nodes, self.rows = [], list(meta), nodes or {}, list(rows)

    def __call__(self, query, params):
        self.calls.append((query, params))
        if query == graph_tools.META_CYPHER:
            return self.meta
        if query == graph_tools.FIND_CYPHER:
            node = self.nodes.get(params["text"].lower())
            return [node] if node else []
        return self.rows


EQ1 = {"node_id": "Equipment:EQ-1", "business_key": "EQ-1", "label": "Pump 1"}


def test_neo4j_store_binds_values_as_parameters():
    runner = FakeRunner(nodes={"eq-1": EQ1}, rows=[{"failure_mode": "BRG-WEAR", "failures": 2}])
    result = Neo4jStore(runner).query(request("equipment_failure_modes", equipment_id="eq-1", limit=5))
    assert result["backend"] == "neo4j" and result["gold_loaded_ts"] == TS and result["rows"] == [
        {"failure_mode": "BRG-WEAR", "failures": 2}]
    query, params = runner.calls[-1]
    assert query == CYPHER["equipment_failure_modes"]          # the fixed template, nothing interpolated
    assert params == {"equipment": "Equipment:EQ-1", "limit": 5}
    assert "EQ-1" not in query                                 # user text only ever travels in params


def test_neo4j_store_failures_after_params():
    runner = FakeRunner(nodes={"bearing wear": {"node_id": "FailureMode:BRG-WEAR", "business_key": "BRG-WEAR",
                                                 "label": "Bearing wear"}})
    Neo4jStore(runner).query(request("failures_after", failure_mode="Bearing wear"))
    assert runner.calls[-1][1] == {"mode": "BRG-WEAR", "type": None, "limit": 20}


def test_neo4j_store_equipment_failure_modes_params():
    runner = FakeRunner(nodes={"eq-1": EQ1})
    store = Neo4jStore(runner)
    store.query(request("equipment_failure_modes"))
    assert runner.calls[-1] == (CYPHER["equipment_failure_modes"], {"equipment": None, "limit": 20})
    store.query(request("equipment_failure_modes", equipment_id="eq-1"))
    assert runner.calls[-1][1] == {"equipment": "Equipment:EQ-1", "limit": 20}


def test_neo4j_store_repeat_failure_chain_params():
    runner = FakeRunner(nodes={"eq-1": EQ1})
    store = Neo4jStore(runner)
    store.query(request("repeat_failure_chain"))
    assert runner.calls[-1] == (CYPHER["repeat_failure_chain"], {"equipment": None, "limit": 20})
    store.query(request("repeat_failure_chain", equipment_id="eq-1"))
    assert runner.calls[-1][1] == {"equipment": "Equipment:EQ-1", "limit": 20}


def test_neo4j_store_refuses_an_empty_graph():
    with pytest.raises(RuntimeError, match="not loaded"):
        Neo4jStore(FakeRunner(meta=())).query(request("equipment_failure_modes", equipment_id="EQ-1"))


def test_neo4j_store_unknown_name_runs_no_template():
    runner = FakeRunner()
    result = Neo4jStore(runner).query(request("equipment_failure_modes", equipment_id="EQ-999"))
    assert result["rows"] == [] and not any(q in CYPHER.values() for q, _ in runner.calls)


# ---------- fallback ----------

class Boom:
    calls = 0

    def __call__(self, query, params):
        Boom.calls += 1
        raise ConnectionError("neo4j down")


def test_service_falls_back_to_memory_and_does_not_retry_during_cooldown(memory):
    Boom.calls = 0
    service = GraphService(Neo4jStore(Boom()), memory, cooldown_seconds=3600)
    first = service.query(request("type_failure_profile", equipment_type="Pump"))
    second = service.query(request("type_failure_profile", equipment_type="Pump"))
    assert first["backend"] == second["backend"] == "memory" and first["rows"]
    assert any("Neo4j was not available" in lim for lim in first["limitations"])
    assert Boom.calls == 1  # the second question did not wait for Neo4j again


def test_service_retries_neo4j_after_the_cooldown(memory):
    Boom.calls = 0
    service = GraphService(Neo4jStore(Boom()), memory, cooldown_seconds=-1)
    service.query(request("type_failure_profile", equipment_type="Pump"))
    service.query(request("type_failure_profile", equipment_type="Pump"))
    assert Boom.calls == 2


def test_service_prefers_neo4j_when_it_answers(memory):
    runner = FakeRunner(nodes={"pump": {"node_id": "EquipmentType:Pump", "business_key": "Pump", "label": "Pump"}})
    result = GraphService(Neo4jStore(runner), memory).query(request("type_failure_profile", equipment_type="Pump"))
    assert result["backend"] == "neo4j" and not any("not available" in lim for lim in result["limitations"])


def test_service_without_neo4j_uses_memory_silently(memory):
    result = GraphService(None, memory).query(request("type_failure_profile", equipment_type="Pump"))
    assert result["backend"] == "memory" and not any("Neo4j" in lim for lim in result["limitations"])


def test_service_raises_when_every_backend_is_down():
    def broken():
        raise ConnectionError("databricks down")

    with pytest.raises(ConnectionError):
        GraphService(Neo4jStore(Boom()), MemoryStore(loader=broken)).query(request("type_failure_profile", equipment_type="Pump"))


def test_make_graph_service_without_neo4j_uri(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    assert graph_tools.make_graph_service().neo4j is None


def test_make_graph_service_with_a_uri_does_not_connect_at_startup(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")  # nothing listens here: building the service must still work
    assert graph_tools.make_graph_service().neo4j is not None


# ---------- the tool ----------

def test_query_graph_tool(monkeypatch, memory):
    monkeypatch.setattr(graph_tools, "_service", GraphService(None, memory))
    out = query_graph.invoke({"template": "type_failure_profile", "equipment_type": "Pump"})
    assert out["template"] == "type_failure_profile" and out["row_count"] == 2 and out["backend"] == "memory"


def test_query_graph_tool_reports_invalid_requests(monkeypatch, memory):
    monkeypatch.setattr(graph_tools, "_service", GraphService(None, memory))
    out = query_graph.invoke({"template": "type_failure_profile"})
    assert out["error"].startswith("Invalid graph request")


# ---------- sync into Neo4j (fake driver) ----------

class FakeSession:
    def __init__(self, log, meta_ts=None):
        self.log, self.meta_ts = log, meta_ts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.log.append((query, params))
        return self

    def consume(self):
        return None

    def single(self):  # the GraphMeta lookup of startup_sync
        return {"ts": self.meta_ts} if self.meta_ts else None


class FakeDriver:
    def __init__(self, meta_ts=None):
        self.log, self.meta_ts = [], meta_ts

    def session(self, database):
        return FakeSession(self.log, self.meta_ts)

    def close(self):
        pass


def test_sync_writes_nodes_then_edges_then_cleans_up_and_stamps():
    driver = FakeDriver()
    done = graph_sync.sync(driver, NODES, EDGES, TS, batch=4)
    queries = [q for q, _ in driver.log]
    assert done["nodes"] == len(NODES) and done["edges"] == len(EDGES) and done["gold_loaded_ts"] == TS
    first_node = next(i for i, q in enumerate(queries) if q.startswith("UNWIND") and ":Node {node_id: row.node_id}" in q)
    first_edge = next(i for i, q in enumerate(queries) if "MATCH (a:Node" in q)
    cleanup = [i for i, q in enumerate(queries) if "DELETE" in q]
    assert first_node < first_edge < min(cleanup) and queries[-1] == graph_sync.META_CYPHER
    # every batch is at most 4 rows, and every row of the tables is written exactly once
    batches = [p["rows"] for q, p in driver.log if "rows" in p]
    assert max(len(b) for b in batches) <= 4
    assert sum(len(b) for q, p in driver.log if "row.node_id" in q for b in [p["rows"]]) == len(NODES)
    assert sum(len(b) for q, p in driver.log if "row.src" in q for b in [p["rows"]]) == len(EDGES)
    # one sync_id for the whole run, and the cleanup deletes everything that has another one
    assert len({p["sync"] for _, p in driver.log if "sync" in p}) == 1


def test_sync_flattens_properties_and_uses_allowlisted_labels():
    driver = FakeDriver()
    graph_sync.sync(driver, NODES, EDGES, TS)
    wo = next(p["rows"] for q, p in driver.log if "SET n:WorkOrder" in q)
    assert wo[0]["props"] == {"created_ts": "2026-01-01T08:00:00.000Z"}
    assert any("MERGE (a)-[r:FOLLOWED_BY]->(b)" in q for q, _ in driver.log)


def test_sync_rejects_unknown_types_before_writing():
    driver = FakeDriver()
    odd_node = {**NODES[0], "node_type": "Site) DETACH DELETE n //"}
    with pytest.raises(ValueError, match="unknown node type"):
        graph_sync.sync(driver, [odd_node], [], TS)
    odd_edge = {**EDGES[0], "edge_type": "X]->() DELETE r //"}
    with pytest.raises(ValueError, match="unknown edge type"):
        graph_sync.sync(driver, NODES, [odd_edge], TS)
    assert driver.log == []


# ---------- sync at API startup ----------

def written(driver):
    return [q for q, _ in driver.log if "MERGE" in q or "DELETE" in q]


def test_startup_sync_loads_neo4j_when_gold_has_a_newer_load(monkeypatch):
    monkeypatch.delenv("GRAPH_SYNC_ON_STARTUP", raising=False)
    driver = FakeDriver(meta_ts="2026-01-01 00:00:00")  # the copy is older than Gold's TS
    result = graph_sync.startup_sync(driver, read_gold_ts=lambda: TS, load=load_fixture)
    assert result.startswith("synced") and written(driver)


def test_startup_sync_loads_an_empty_neo4j(monkeypatch):
    monkeypatch.delenv("GRAPH_SYNC_ON_STARTUP", raising=False)
    driver = FakeDriver(meta_ts=None)
    assert graph_sync.startup_sync(driver, read_gold_ts=lambda: TS, load=load_fixture).startswith("synced")
    assert written(driver)


def test_startup_sync_does_nothing_when_the_copy_is_current(monkeypatch):
    monkeypatch.delenv("GRAPH_SYNC_ON_STARTUP", raising=False)
    driver = FakeDriver(meta_ts=TS)

    def never_loaded():
        raise AssertionError("must not read the Gold graph tables")

    result = graph_sync.startup_sync(driver, read_gold_ts=lambda: TS, load=never_loaded)
    assert result.startswith("up to date") and not written(driver)


def test_startup_sync_skips_empty_gold_tables(monkeypatch):
    monkeypatch.delenv("GRAPH_SYNC_ON_STARTUP", raising=False)
    driver = FakeDriver()
    assert "empty" in graph_sync.startup_sync(driver, read_gold_ts=lambda: None, load=load_fixture)
    assert not written(driver)


@pytest.mark.parametrize("value", ["false", "0", "No", "OFF"])
def test_startup_sync_can_be_switched_off(monkeypatch, value):
    monkeypatch.setenv("GRAPH_SYNC_ON_STARTUP", value)
    driver = FakeDriver()
    assert "off" in graph_sync.startup_sync(driver, read_gold_ts=lambda: TS, load=load_fixture)
    assert driver.log == []


def test_startup_sync_without_neo4j_uri(monkeypatch):
    monkeypatch.delenv("GRAPH_SYNC_ON_STARTUP", raising=False)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    assert "NEO4J_URI is not set" in graph_sync.startup_sync()


def test_background_sync_does_nothing_without_neo4j_uri(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # unit tests never read the real .env
    monkeypatch.delenv("NEO4J_URI", raising=False)
    assert graph_sync.start_background_sync() is None


def test_background_sync_runs_in_a_thread_and_survives_errors(monkeypatch, caplog):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")

    def broken():
        raise ConnectionError("neo4j down")

    monkeypatch.setattr(graph_sync, "startup_sync", broken)
    with caplog.at_level("INFO", logger="app.graph_sync"):
        thread = graph_sync.start_background_sync()
        thread.join(timeout=5)
    assert thread.daemon and not thread.is_alive()
    assert [r.getMessage() for r in caplog.records] == ["graph_sync_started", "graph_sync_failed"]
