# FastAPI integration layer

**Spec:** §6.7 REST APIs, FastAPI and Azure Integration · **Owner:** @participant3
**Deliverable:** FastAPI integration → `docs/fastapi_integration.md`

**Acceptance condition (§6.7 / §7):** validated endpoints with Pydantic request/response models,
consistent error responses, OpenAPI docs at `/docs`, API security, and executable request examples
in `postman/`. The API is connected end to end with Gold / the semantic layer and the LangChain tools;
any mocked cloud dependency is clearly documented.

## Minimum endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (stub exists in `main.py`) |
| GET | `/ready` | Readiness: dependencies reachable |
| POST | `/analytics/query` | Approved KPI / aggregate queries over Gold |
| POST | `/assistant/query` | LangChain Core assistant (see `src/app/agent/`) |
| _TBD_ | domain endpoint | e.g. `GET /equipment/{equipment_id}/health` |

## Cross-cutting requirements

- API key auth in dev; OAuth2 bearer / JWT validation in the deployed design
- Correlation ID per request; structured logs with no secrets or sensitive data
- External calls: timeouts, bounded retries, pagination and rate-limit handling
