# Gold KPIs -> frontend graphs (plan: ~/.claude/plans/check-src-app-api-cause-i-jazzy-shamir.md)

Decisions: keep `databricks-sql-connector` (GoldDB); server-side display conversions; JWT in HttpOnly+Secure cookie,
users added by hand (`AUTH_USERS`), no new library.

- [x] 1. Confirm warehouse reachable (64,431 KPI rows, 2026-03..2026-09, as_of 2026-09-21; 11.6 s cold)
- [x] 2. Data dictionary: cost/maintenance-type columns exist; status_counts derived from availability
- [x] 3. `dashboard.py` + schemas + routes + `tests/unit/test_dashboard.py` (reconciled vs raw SQL)
- [x] 4. Cookie auth: config + security + `/auth/*` router + `hash_password` CLI + `tests/unit/test_auth.py`
- [x] 5. Frontend: login form, `client.js`/`auth.jsx`, Vite proxy rewrite; Fleet Health + Maintenance on real data
- [ ] 6. Docs (`api/README.md`, stale `CLAUDE.md`) + `.env.example`
- [ ] Verify: ruff, pytest -m "not integration" (rerun after config.py REPO_ROOT change), user browser check

## Phase 2: the four pages still pending (Equipment, Sensor Trends, Alarms, Work orders)
Facts: 9,298 assets (mock has 12); Gold ends 2026-09-21 so "last 1h/24h/7d" must end at the data's latest
timestamp, not now; Gold has no warn/crit limits, health score, RUL or acknowledgements.
- [ ] 7. Profile Gold tables (agent running): sensor volume/sampling, alarms count, columns, query timings
- [ ] 8. Design endpoints + response shapes (paging/search for 9k assets, bucketing for sensor series)
- [ ] 9. Backend (tests first): /assets, /assets/{id}, /assets/{id}/sensors, /alarms, /maintenance/work-orders
- [ ] 10. Frontend: replace `pending(...)` per endpoint; make optional fields (warn/crit, health, RUL) render absent
- [ ] 11. Reconcile vs raw SQL, ruff, pytest, user browser check

## Review
(filled in at the end)
