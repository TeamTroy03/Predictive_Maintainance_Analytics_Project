# 0002: Knowledge graph derived from Gold, queried through Neo4j with an in-memory fallback

* Status: accepted
* Date: 2026-10-13
* Owner: AI and API (graph tables: data engineering)

## Context

The spec's bonus asks for a simple knowledge graph of the domain with one graph query exposed through the assistant. The
star schema answers "how much" well (KPIs by month, site, type) but relationship questions need joins that a user cannot
express and a planner should not improvise: which failure modes an asset or a type has, which failures repeat, what tends
to fail after what. The assistant must still not fabricate data or bypass the governed layer.

## Decision

* **The graph is a derived read model of Gold, not a new source and not LLM-inferred.** Nodes are dimension rows and
  derived entities, edges follow the Gold keys plus a few derived links (`REPEATED_FAILURE` reuses Gold's
  `is_repeat_failure` flag, `FOLLOWED_BY`, `EXPERIENCED_FAILURE`, `TYPE_PRONE_TO`). It is stored in two Delta tables built
  in Databricks from the Gold tables, `<prefix>gold_kg_nodes` and `<prefix>gold_kg_edges` (documented in the Gold data dictionary),
  with the same rules as every Gold table: full overwrite, `gold_loaded_ts`, reconciliation against the facts and dimensions.
  This repo does not build them, it only reads them.
  An LLM inferring nodes and edges was rejected: it can invent or drop relations, cannot be reconciled, is not reproducible
  and costs tokens per row.
* **Neo4j is the query engine, Delta stays the source of truth.** `graph_sync.py` copies the two tables into Neo4j
  idempotently (`MERGE`, run id, delete what Gold no longer has). The assistant uses Neo4j when `NEO4J_URI` is set.
* **Automatic fallback to an in-process NetworkX graph** built from the same Gold tables, like the conversation memory
  falls back from Redis: no `NEO4J_URI`, Neo4j down, bad credentials or an empty database, and the question is answered
  from memory, with a limitation and `backend: "memory"` in the result. Neo4j is not retried for 60 s after a failure.
* **One tool, `query_graph`, with a closed set of templates** (`equipment_failure_modes`, `type_failure_profile`,
  `repeat_failure_chain`, `failures_after`). The LLM picks a template and typed arguments; it never writes Cypher. Arguments
  are validated by `GraphRequest` in `check_plan` and in the tool; Neo4j queries are fixed strings with bound parameters in a
  read session. The human approval step and the number check are unchanged.
* The graph answers relationship questions. Figures over periods stay with `get_kpi`, so measure logic is not duplicated.

## Consequences

* Graph answers cannot disagree with Gold: same population as the KPI failures (unplanned downtime with a failure mode,
  known equipment), checked against the Gold tables (`REPEATED_FAILURE` edges equal flagged rows, weights equal failure counts).
  Verified on the delivered data: 20,643 nodes and 49,260 edges, and a failure count per type and mode equal to a direct
  count on `fact_downtime`.
* **Two implementations of every template** (Cypher and Python) can drift apart. This is guarded by a contract test that runs
  the same fixture through both backends and compares rows, filters and limitations (`tests/integration/test_graph_neo4j.py`,
  needs a throwaway Neo4j). Adding a template means adding it in both places and to that test.
* Neo4j is a copy, only as fresh as the last sync. The API runs the sync itself at startup, in a background thread, and only when
  Gold's `gold_loaded_ts` is newer than the one stored in Neo4j (a restart without a new Gold run does not reload it); until it has
  finished the assistant answers from the in-memory graph. After a new Gold run, restart the API or run `graph_sync` by hand: there is
  no periodic refresh yet. The result carries the `gold_loaded_ts` it came from. The in-memory fallback is rebuilt from Delta
  (cache 1 h), so it can be fresher than Neo4j.
* The sync replaces the whole target database, so Neo4j needs a database of its own. Credentials are environment variables
  locally and Key Vault references when deployed; the hosting choice (container, Aura) is open and needs no code change.
* Not covered: asset similarity and multi-hop path search are not templates yet (derivable later from the same graph);
  sensor readings are not nodes (only per-sensor aggregates); Neo4j is not part of CI or of the Docker image.
* Alternatives rejected: LLM-inferred graph (above); only an in-memory graph (no graph engine to show or to scale);
  Neo4j only, with no fallback (the assistant would lose the tool whenever Neo4j is down); letting the LLM write Cypher
  (would bypass the allowlist and the review of what runs).
