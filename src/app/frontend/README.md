# AssetPulse – Predictive Maintenance frontend (prototype)

A simple React + Vite app for the 5 required views:

| Page | URL | What it shows |
|---|---|---|
| Fleet Health | `/` | 8 KPIs, asset status counts, availability trend, assets needing attention |
| Critical Exceptions | `/alarms` | Active alarms sorted by urgency, Acknowledge, Ask AI |
| Equipment | `/equipment` | Searchable asset list with status filter |
| Equipment drill-through | `/equipment/:id` | Asset KPIs, latest readings, components, sensor charts, alarms, maintenance history (RUL only when the backend sends it) |
| Sensor Trends | `/sensors?asset=…&range=30d` | Stacked sensor charts with synced hover, warning/critical limits where Gold has them, anomaly dots; 24 h / 7 days / 30 days (default) / all, counted back from the asset's last reading |
| Maintenance | `/maintenance` | MTBF/MTTR trend, cost by type, work orders with status filter |

The **AI Assistant** panel opens on the right side (button in the top bar, or the "Ask AI" buttons).
It sends every question together with what the user is looking at (page, asset, time range).

## Sign in

The app shows only the **Sign in** page until `GET /api/auth/me` returns a user: no page, chart or API call renders before that.
Users are added by hand on the server (`AUTH_USERS`, see `src/app/api/README.md`, section "Website login"). In demo mode
(below) any sign-in works.

## Run it

```bash
npm install
npm run dev          # http://localhost:5173
```

The app talks to the real FastAPI by default. Vite forwards every `/api/...` call to `VITE_API_TARGET`
(default `http://localhost:8000`) and removes the `/api` prefix (FastAPI serves `/fleet/summary`, not `/api/fleet/summary`), so the
browser only talks to the dev server: the session cookie stays same-origin and CORS is not needed.

1. Start FastAPI from the **repository root**: `uvicorn app.api.main:app --reload` (port 8000).
2. Optional: create `src/app/frontend/.env` (there is no template file) with
   ```
   VITE_API_TARGET=http://localhost:8000    # where FastAPI runs
   VITE_USE_MOCK=true                       # demo data without any backend (default: false)
   VITE_API_BASE_URL=https://your-host      # only for a deployed build, instead of the dev proxy
   ```
3. Restart `npm run dev` after changing `.env`.

## Where things are

```
src/
  api/client.js        ← the ONLY file that calls the backend (all endpoints listed here)
  api/mockData.js      ← demo data (VITE_USE_MOCK=true), a superset of what the backend sends
  components/          ← Layout (sidebar + top bar), AgentPanel, SensorCharts, ui (cards, tables, badges)
  pages/               ← one file per page
  hooks/useApi.js      ← small helper for loading / error states
  auth.jsx             ← signed-in user (calls /api/auth/me), login and logout
  asOf.jsx · format.js ← the data's "as of" date shown in the pages, number and date formatting
```

## API contract (what the frontend calls)

Implemented by `src/app/api` (see its README); the response models are in `src/app/api/schemas.py`. All times are ISO 8601 strings.
Status values: `operational`, `warning`, `critical`, `offline`. A field the backend leaves out is simply not shown.

| Method | Endpoint | Returns |
|---|---|---|
| GET | `/api/auth/me` | `{ email, name, role }` for the signed-in user (session cookie), `401` if nobody is signed in |
| POST | `/api/auth/login` | body `{ email, password }` → the same user object and the session cookie (`429` after repeated failures, `503` if login is not configured) |
| POST | `/api/auth/logout` | Ends the session |
| GET | `/api/fleet/summary` | `{ as_of, period_from, period_to, kpis: { availability_pct, unplanned_downtime_h, mtbf_h, mttr_h, repeat_failure_pct, overdue_work_orders, maintenance_cost_eur, alarm_rate_per_h }, status_counts: { operational, warning, critical, offline }, availability_trend: [{ date, availability_pct, unplanned_downtime_h }] }` |
| POST | `/api/analytics/query` | body `{ kpis, group_by, filters, limit }` → governed KPIs grouped, `{ rows, ... }` |
| GET | `/api/assets?status=&q=&has_sensors=&page=&page_size=` | `{ items: [{ id, name, type, line, site, status, availability_pct, manufacturer, criticality, installed, active_alarms, last_maintenance }], total, page, page_size }` |
| GET | `/api/assets/{id}` | the asset fields above + `{ kpis: { availability_pct, mtbf_h, mttr_h, unplanned_downtime_h }, latest_readings: [{ sensor, value, unit, status }], components: [] }` |
| GET | `/api/assets/{id}/sensors?range=24h` | `{ asset_id, range, sensors: [{ id, name, unit, warn, crit, lower_is_bad, points: [{ t, value, anomaly }] }] }`; `range` is 1h, 24h, 7d, 30d or all; `value` is `null` for missing data |
| GET | `/api/maintenance/summary` | `{ as_of, period_from, period_to, kpis: { mtbf_h, mttr_h, overdue_work_orders, maintenance_cost_eur, repeat_failure_pct, planned_pct }, monthly: [{ month, mtbf_h, mttr_h, cost_eur }], cost_by_type: [{ type, cost_eur }] }` |
| GET | `/api/maintenance/work-orders?status=&asset_id=&page=&page_size=` | `{ items: [{ id, asset_id, type, status (open, overdue, completed or cancelled), priority, title, opened, due, completed, downtime_h, cost }], total, page, page_size }` (page_size default 20, max 100); no technician column unless the data has one |
| GET | `/api/alarms?asset_id=` | `[{ id, asset_id, severity (critical or warning), sensor, message, value, threshold, unit, time, acknowledged, state (active or cleared) }]` |
| POST | `/api/assistant/query` | body `{ question, conversation_id }` (max 1000 chars; the panel writes the page context in front of the question; `conversation_id` is one random ID per page load, it gives the agent memory of earlier questions) → `{ status: "awaiting_approval", run_id, plan }`, or already `{ status: "answered", result }` when the plan is out of scope |
| POST | `/api/assistant/approve` | body `{ run_id, approved }` → `{ status: "answered", result: { answer, evidence: [{ tool, arguments, status, result }], grounded, limitations: [], filters: {}, source_measure } }` |

**Not available yet:** acknowledging an alarm. Gold keeps no acknowledgements, so the button only works in demo mode.
The mock data (`api/mockData.js`) still has fields the backend does not send (health score, run hours, RUL, technician, asset names).

The agent proposes a plan first and only runs it after the user approves (Approve / Decline buttons in the panel).
Each call can take up to a few minutes, so the client timeout is 300 s. Errors use
`{ error: { code, message, correlation_id } }`; `unknown_run` (404) means the plan expired, ask again. A `401` outside the
`/api/auth/` calls reloads the page, which then shows the Sign in page.

## Next steps (when needed)

- Stream the agent's answer word by word (Server-Sent Events) instead of waiting for the full reply.
- Live alarm updates (the Alarms page loads once, there is no polling or stream yet).
- Pages that still need data Gold does not have: health score, remaining useful life, warning/critical limits per sensor.
