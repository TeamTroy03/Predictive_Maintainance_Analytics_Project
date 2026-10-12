# AssetPulse – Predictive Maintenance frontend (prototype)

A simple React + Vite app for the 5 required views:

| Page | URL | What it shows |
|---|---|---|
| Fleet Health | `/` | 8 KPIs, asset status counts, availability trend, assets needing attention |
| Critical Exceptions | `/alarms` | Active alarms sorted by urgency, Acknowledge, Ask AI |
| Equipment | `/equipment` | Searchable asset list with status filter |
| Equipment drill-through | `/equipment/:id` | Asset KPIs, RUL, readings, components, sensor charts, alarms, maintenance history |
| Sensor Trends | `/sensors?asset=…&range=24h` | Stacked sensor charts with synced hover, warning/critical limits, anomaly dots, 1h / 24h / 7d |
| Maintenance | `/maintenance` | MTBF/MTTR trend, cost by type, work orders with status filter |

The **AI Assistant** panel opens on the right side (button in the top bar, or the "Ask AI" buttons).
It sends every question together with what the user is looking at (page, asset, time range).

## Run it

```bash
npm install
npm run dev          # http://localhost:5173
```

It starts with **demo data**, so it works without a backend.

## Connect it to your FastAPI

1. Start FastAPI on port 8000 (e.g. `uvicorn main:app --reload --port 8000`).
2. Open `.env` and set:
   ```
   VITE_USE_MOCK=false
   VITE_API_TARGET=http://localhost:8000
   ```
3. Restart `npm run dev`. The sidebar shows "Connected to FastAPI".

During development Vite forwards every `/api/...` call to FastAPI (see `vite.config.js`), so you don't need CORS.

## Where things are

```
src/
  api/client.js        ← the ONLY file that calls the backend (all endpoints listed here)
  api/mockData.js      ← demo data, same JSON shapes the backend must return
  components/          ← Layout (sidebar + top bar), AgentPanel, SensorCharts, ui (cards, tables, badges)
  pages/               ← one file per page
  hooks/useApi.js      ← small helper for loading / error states
```

## API contract (what FastAPI must return)

All times are ISO 8601 strings. Status values: `operational | warning | critical | offline`.

| Method | Endpoint | Returns |
|---|---|---|
| GET | `/api/auth/me` | `{ name, email, role }` for the signed-in user (session cookie), `401` if nobody is signed in. Until it returns a user the app shows only the Sign in page, no charts. |
| GET | `/api/auth/login` | Browser redirect (not fetch): FastAPI starts the OAuth flow and sends the user back to the app signed in |
| POST | `/api/auth/logout` | Ends the session |
| GET | `/api/fleet/summary` | `{ as_of, kpis: { availability_pct, unplanned_downtime_h, mtbf_h, mttr_h, repeat_failure_pct, overdue_work_orders, maintenance_cost_eur, alarm_rate_per_h }, status_counts: { operational, warning, critical, offline }, availability_trend: [{ date, availability_pct, unplanned_downtime_h }] }` |
| GET | `/api/assets` | `[{ id, name, type, line, status, health_score, run_hours, manufacturer, installed, active_alarms, last_maintenance }]` |
| GET | `/api/assets/{id}` | asset fields + `{ kpis: { availability_pct, mtbf_h, mttr_h, unplanned_downtime_h }, rul_days, predicted_failure, components: [{ name, status }], latest_readings: [{ sensor, value, unit, status }] }` |
| GET | `/api/assets/{id}/sensors?range=1h\|24h\|7d` | `{ asset_id, range, sensors: [{ id, name, unit, warn, crit, lower_is_bad, points: [{ t, value, anomaly }] }] }` (use `value: null` for missing data; keep a few hundred points per sensor) |
| GET | `/api/maintenance/summary` | `{ kpis: { mtbf_h, mttr_h, overdue_work_orders, maintenance_cost_eur, repeat_failure_pct, planned_pct }, monthly: [{ month, mtbf_h, mttr_h, cost_eur }], cost_by_type: [{ type, cost_eur }] }` |
| GET | `/api/maintenance/work-orders?status=&asset_id=` | `[{ id, asset_id, asset_name, type, status (open\|overdue\|completed), priority, title, technician, opened, due, completed, downtime_h, cost }]` |
| GET | `/api/alarms?asset_id=` | `[{ id, asset_id, asset_name, severity (critical\|warning\|info), sensor, message, value, threshold, unit, time, acknowledged }]` |
| POST | `/api/alarms/{id}/ack` | `{ ok: true }` |
| POST | `/api/assistant/query` | body `{ question }` (max 1000 chars; the panel writes the page context in front of the question) → `{ status: "awaiting_approval", run_id, plan }`, or already `{ status: "answered", result }` when the plan is out of scope |
| POST | `/api/assistant/approve` | body `{ run_id, approved }` → `{ status: "answered", result: { answer, evidence: [{ tool, arguments, status, result }], grounded, limitations: [], filters: {}, source_measure } }` |

The agent proposes a plan first and only runs it after the user approves (Approve / Decline buttons in the panel).
Each call can take up to a few minutes, so the client timeout is 300 s. Errors use
`{ error: { code, message, correlation_id } }`; `unknown_run` (404) means the plan expired, ask again.

## Next steps (when needed)

- Stream the agent's answer word by word (Server-Sent Events) instead of waiting for the full reply.
- Replace alarm polling (every 30 s) with a live stream.
- Add login if the app goes beyond a demo.
