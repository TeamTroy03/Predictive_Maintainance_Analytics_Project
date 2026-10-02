# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Team 3 capstone (Accenture / Code.Hub "AI and Data"): predictive maintenance analytics. The spec is `context/*.pdf`; every folder's `README.md` states the spec section (§), owner and acceptance condition for that area, and the root `README.md` has the spec → repo map. Read the relevant folder README before implementing anything in it.

Most of the repo is currently scaffolding (READMEs and `_TODO_` doc templates). The only real code is the `/health` route (`src/app/api/routes/health.py`, wired in `src/app/api/main.py`) and `tests/unit/test_health.py`; the other modules under `src/` are docstring-only stubs naming their responsibility.

## Commands

Requires Python 3.14+. Dependencies are declared in `pyproject.toml` (`uv.lock` is also present).

```bash
pip install -e ".[dev]"                 # app + pytest + ruff
ruff check .                            # lint
pytest                                  # all tests
pytest tests/unit/test_health.py::test_health_returns_ok   # single test
pytest -m "not integration"             # what CI runs (integration needs a deployed env)
uvicorn app.api.main:app --reload       # http://localhost:8000/docs
docker build -t pm-api .                # image built as the CI artifact
```

Pytest markers `data_quality` and `integration` are referenced in `tests/README.md` but not yet registered in `pyproject.toml`; register them there when adding such tests. There is no ruff configuration yet (defaults apply).

## Architecture

Data flow (spec §5): `src/data/raw/` (3 CSV + 1 nested JSON, synthetic with deliberately injected defects) → Databricks **Bronze** (raw, append-only Delta + ingestion metadata) → **Silver** (validated, deduplicated, conformed, with quarantine for rejects) → **Gold** (Delta star schema) → Microsoft Fabric semantic model → Power BI report.

The service layer sits *on top of Gold / the semantic model*, not on raw data:
- `src/app/api/` — FastAPI app (`app.api.main:app`): `routes/` (one router per endpoint group), `schemas/` (Pydantic models). Routes call `src/app/services/`; cross-cutting config/logging/auth live in `src/app/core/`. Planned endpoints: `/health`, `/ready`, `POST /analytics/query`, `POST /assistant/query`, plus a domain endpoint. Requires Pydantic models, consistent error shape, correlation IDs, API-key auth in dev / JWT in deployed design.
- `src/app/agent/` (`prompts/`, `tools/`, `agent.py`, `schemas.py`) — LangChain **Core only** (`langchain-core`, not full LangChain): typed tools bound to a chat model, tool-call validation, `ToolMessage` round-trip, structured output (`answer`, `evidence`, `filters`, `source_or_measure`, `limitations`). The LLM must not bypass the semantic layer, fabricate data, or take consequential actions. `/assistant/query` reaches it via `services/assistant_service.py`.

Cross-layer conventions (from the folder READMEs):
- `databricks/common/` is the single shared implementation of audit/lineage columns (ingestion timestamp, batch ID, source filename, record hash, load status, run metrics) used by Bronze, Silver and Gold so lineage is consistent end to end. Environment config (`dev`/`prod` catalog/schema/paths) lives there too; no secrets.
- `databricks/` is intentionally **not** a Python package (no `__init__.py`) so it doesn't shadow the `databricks` namespace of `databricks-sdk`/Databricks Connect. Don't add one. Python code uses a `src/` layout: `src/app` and `src/data` are packaged (`[tool.setuptools.packages.find]`, `where = ["src"]`); pytest adds `src` to the path.
- Config comes from environment variables (names in `.env.example`; copy to `.env`, which is gitignored). Deployed secrets come from Azure Key Vault.
- Tests: `tests/unit` (CI), `tests/data_quality` (CI, local fixtures), `tests/integration` (manual / deployed env only).
- Material architectural decisions and scope changes get an ADR in `docs/adr/` (copy `0000-adr-template.md` to `NNNN-short-title.md`).

## Repo gotchas

- `.gitignore` ignores `.github/workflows/*` and `.github/CODEOWNERS`, so CI workflow files and CODEOWNERS are not tracked by git as it stands (CODEOWNERS is absent from `git ls-files`). Use `git add -f` or fix `.gitignore` if these need to be committed.
- `CODEOWNERS` and docs use placeholder handles `@participant1`–`@participant5` (roles: data engineering, analytics, AI and API, cloud/DevOps, PM); ownership follows spec §8.
- `.venv/`, `.ruff_cache/`, `.pytest_cache/` and `*.egg-info/` exist locally and are gitignored; exclude them when searching.

## Problem context
Summary of what we're solving: @.claude/skills/The_task/PROBLEM.md
The full original spec is at .claude/skills/The_task/problem_statement.pdf. Consult it when
requirements are ambiguous or details are missing from the summary.