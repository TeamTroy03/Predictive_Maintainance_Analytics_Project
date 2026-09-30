# Tests

**Spec:** §7 Testing and operations; supports §6.2, §6.3, §6.7, §6.8
**Deliverable:** `docs/11-testing-operations.md`

| Folder | Runs in CI | Marker | Owner |
|---|---|---|---|
| `unit/` | yes | — | everyone |
| `data_quality/` | yes (local fixtures) | `data_quality` | @participant1, @participant2 |
| `integration/` | no (manual / deployed env) | `integration` | @participant4 |

CI runs `pytest -m "not integration"`.
