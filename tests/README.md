# Tests

**Spec:** §7 Testing and operations; supports §6.2, §6.3, §6.7, §6.8
**Deliverable:** `docs/runbook.md`

| Folder | Runs in CI | Marker |
|---|---|---|
| `unit/` | yes | — |
| `data_quality/` | yes (local fixtures) | `data_quality` |
| `integration/` | no (manual / deployed env) | `integration` |

CI runs `pytest -m "not integration"`.

The markers `integration` and `data_quality` are registered in `pyproject.toml`. Run everything CI runs with
`pytest -m "not integration"`.

## Known failures

Three tests in `tests/unit/test_auth.py` (`test_login_sets_httponly_secure_strict_cookie`,
`test_login_unavailable_when_not_configured`, `test_prod_starts_with_safe_auth`) fail in a full `pytest` run but pass when
`test_auth.py` is run alone, so they appear to depend on state left by other tests (cause not investigated).

## Knowledge graph tests

```bash
uv run pytest tests/unit/test_graph_tools.py                                  # in CI: no Neo4j, Databricks or LLM
NEO4J_TEST_URI=bolt://127.0.0.1:7687 NEO4J_TEST_PASSWORD=<test-password> \
  uv run pytest tests/integration/test_graph_neo4j.py -m integration -v        # contract test against a throwaway Neo4j
```

The contract test loads the same small fixture graph into Neo4j and checks that the Neo4j and in-memory backends of `query_graph`
return identical rows for every template. It replaces everything in the target database, so it only reads `NEO4J_TEST_URI`
(never `NEO4J_URI`) and is skipped without it. Start the throwaway container with the command in
`src/app/agent/tools/README.md`.
