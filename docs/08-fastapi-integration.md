# 08 — FastAPI integration

**Spec:** §7 Deliverable "FastAPI integration"; §6.7 · **Owner:** @participant3

**Required evidence:** validated endpoints, OpenAPI docs, error handling, API security and
executable request examples.

## Endpoints
| Method | Path | Request model | Response model | Status codes |
|---|---|---|---|---|
| GET | `/health` | — | | 200 |
| GET | `/ready` | — | | 200 / 503 |
| POST | `/analytics/query` | | | |
| POST | `/assistant/query` | | | |
| | _domain endpoint_ | | | |

## Error response format
_TODO_

## Security
_TODO: API key (dev), OAuth2 / JWT (deployed)._

## Observability
_TODO: correlation IDs, structured logging, what is never logged._

## External API resilience
_TODO: timeouts, retries, pagination, rate limits._

## OpenAPI and executable examples
- Swagger UI: `/docs`
- Postman: `postman/capstone.postman_collection.json`

## Mocked dependencies
_TODO: list any mocks and why._
