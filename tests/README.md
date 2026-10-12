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

`tests/unit/test_detail_tools.py::test_get_kpi_schema` fails (stale expectation of the old single-table schema, see
`src/app/agent/README.md`). Three tests in `tests/unit/test_auth.py` (`test_login_sets_httponly_secure_strict_cookie`,
`test_login_unavailable_when_not_configured`, `test_prod_starts_with_safe_auth`) fail in a full `pytest` run but pass when
`test_auth.py` is run alone, so they appear to depend on state left by other tests (cause not investigated).
