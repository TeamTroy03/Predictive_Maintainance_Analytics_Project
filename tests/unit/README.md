# Unit tests

**Spec:** §7 Testing and operations ("unit tests")

**Acceptance condition:** pure-logic code (transform functions, DQ rule functions, Pydantic models,
tool argument validation, API routes via `TestClient`) is tested without external services and
passes in the CI `test` job.

## Files

| File | Covers |
|---|---|
| `test_health.py` | `/health` |
| `test_auth.py` | cookie login (JWT in an HttpOnly cookie), `AUTH_USERS`, throttling |
| `test_dashboard.py`, `test_assets.py` | the page endpoints (`/fleet/summary`, `/maintenance/summary`, `/assets*`, `/alarms`, `/maintenance/work-orders`) with a fake database |
| `test_assistant.py` | `/assistant/query` and `/assistant/approve` with a fake agent (no LLM) |
| `test_detail_tools.py` | the SQL guardrail of the agent's `query_kpi_table` tool |
| `test_continuation.py` | the data generator's `--continue` mode: no id reuse, no downtime overlap with delivered data, labor cost vs Silver's fixed FX rates, assets Silver quarantines |
| `test_upload_to_databricks.py` | the volume upload and its conflict check, with a fake client (no network) |
| `test_silver_check_paths.py` | the Silver-check tool resolves its default paths (no Spark needed) |
