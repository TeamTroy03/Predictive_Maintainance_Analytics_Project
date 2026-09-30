# Unit tests

**Spec:** §7 Testing and operations ("unit tests") · **Owner:** everyone (code authors)

**Acceptance condition:** pure-logic code (transform functions, DQ rule functions, Pydantic models,
tool argument validation, API routes via `TestClient`) is tested without external services and
passes in the CI `test` job.
