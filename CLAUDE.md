# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Team 3 capstone (Accenture / Code.Hub "AI and Data"): predictive maintenance analytics. The spec is `context/*.pdf`; every folder's `README.md` states the spec section (§), owner and acceptance condition for that area, and the root `README.md` has the spec → repo map. Read the relevant folder README before changing anything in it.

What is real today: the synthetic data generator and uploader (`src/data/`), the Databricks Bronze notebook and the four Silver scripts (`databricks/`), the FastAPI service with login (`src/app/api/`), the LangGraph assistant (`src/app/agent/`), the React frontend (`src/app/frontend/`) and the unit tests. Still scaffolding or missing: `databricks/common/` and `jobs/` (README only), `databricks/golden/` (placeholder SQL; the real Gold notebook is a reference copy in `.claude/reference/`), `fabric/` (model diagrams only, no TMDL or report), `infra/` (Bicep skeleton without resources), the CI workflows (`.github/workflows/` does not exist yet, only `CODEOWNERS`) and most of `docs/` (only `docs/adr/0001-continuation-runs.md`).

## Commands

Uses **uv**, not pip: Python 3.14 comes from `.python-version`, dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Add a dependency with `uv add <package>` (or `uv add --optional dev <package>`) so both files change together.

```bash
uv sync --extra dev                     # .venv with the locked dependencies (app + pytest + ruff); add --extra databricks for the volume upload
uv run ruff check <files>               # lint
uv run pytest -m "not integration"      # what CI should run (integration needs Azure OpenAI + Databricks, spends LLM quota)
uv run pytest tests/unit/test_continuation.py::test_ids_continue_after_the_baseline   # single test
uv run uvicorn app.api.main:app --reload   # http://localhost:8000/docs, run from the repo root
(cd src/app/frontend && npm install && npm run dev)   # http://localhost:5173, proxies /api to the API
docker build -t pm-api .                # image built as the CI artifact
```

Markers `integration` and `data_quality` are registered in `pyproject.toml`. There is no ruff configuration in the repo and the existing code has many findings, so lint only the files you touch. Known test failures that predate recent work: `tests/unit/test_detail_tools.py::test_get_kpi_schema`, and three tests in `tests/unit/test_auth.py` that fail in a full run but pass alone.

Data tooling (see `src/data/README.md` and `src/data/generate/README.md`):

```bash
uv run python src/data/generate/generate_data.py --continue --rows-per-file 1000 --days 21          # new activity for the delivered plant
uv run python src/data/generate/generate_data.py --continue --days 21 --upload --folder test_datasets   # ... and upload it (needs --extra databricks)
uv run python src/data/upload/upload_to_databricks.py --folder test_datasets --dry-run               # preview, connects to nothing
python src/data/generate/checks/silver_check.py --tags 2     # Silver rules on the files: run with the python of the separate Spark environment (see its docstring), not uv run
```

## Architecture

Data flow (spec §5): `src/data/raw/` (3 CSV + 1 nested JSON, synthetic with deliberately injected defects) → Databricks **Bronze** (Auto Loader, append/MERGE by `record_hash`, audit table) → **Silver** (validated, deduplicated, conformed, with quarantine) → **Gold** (Delta star schema, tables `<prefix>gold_*`, prefix `test_` today) → Microsoft Fabric semantic model → Power BI report. Catalog `data2026acc123456`, schema `team3`; Bronze reads the volume folder `/Volumes/data2026acc123456/team3/datasets/<dataset>/`, the combined test notebook `.claude/reference/test_pipeline.ipynb` reads `test_datasets/` and writes `test_*` tables.

The service layer sits *on top of Gold*, not on raw data:
- `src/app/api/` — FastAPI app (`app.api.main:app`). Flat modules, not packages: `routes.py` (all endpoints in three routers: public, `/auth`, authenticated), `schemas.py` (Pydantic), `services.py` / `kpis.py` / `dashboard.py` / `assets.py` (Gold queries and page payloads), `db.py` (the only module that talks to Databricks), `security.py`, `errors.py`, `observability.py` (correlation IDs). Auth: session cookie for the website (users from `AUTH_USERS`, JWT in an HttpOnly cookie), `X-API-Key` for development, Entra ID bearer JWT for the deployed design. `src/app/core/` holds `config.py` (settings from env / `.env`), `logging.py` (JSON logs) and `security.py` (password, session and JWT logic). `src/app/services/` contains two docstring-only stubs that nothing imports.
- `src/app/agent/` (`agent.py`, `memory.py`, `schemas.py`, `prompts/`, `tools/`) — a LangGraph planner → evaluator → **human approval** → executor flow using `langchain-core` messages and `AzureChatOpenAI`. The LLM never calls a tool directly: it writes a plan, a deterministic check and a judge LLM review it, a human approves, then only the allowlisted tools (`get_kpi`, `query_kpi_table`) run. The LLM must not bypass the semantic layer, fabricate data or take consequential actions. `bind_tools` is not used (differs from the spec wording; see the agent README). `/assistant/query` and `/assistant/approve` reach it through `src/app/api/agent.py`; run **one uvicorn worker** (pending plans live in memory). Follow-up questions work through `conversation_id` memory (`memory.py`: Redis if `REDIS_URL` is set, else in the process); the planner and judge prompts also receive today's date.
- `src/app/frontend/` — React + Vite app (AssetPulse). Only `src/api/client.js` talks to the backend; Vite forwards `/api/...` to FastAPI and strips the prefix. Real backend by default, `VITE_USE_MOCK=true` for demo data.

Cross-layer conventions (from the folder READMEs):
- `databricks/common/` is meant to be the single shared implementation of audit/lineage columns (ingestion timestamp, batch ID, source filename, record hash, load status, run metrics); it is still empty, each notebook has its own copy. Environment config (`dev`/`prod`) is meant to live there too; no secrets.
- `databricks/` is intentionally **not** a Python package (no `__init__.py`) so it doesn't shadow the `databricks` namespace of `databricks-sdk`/Databricks Connect. Don't add one. Python code uses a `src/` layout: `src/app` and `src/data` are packaged (`[tool.setuptools.packages.find]`, `where = ["src"]`); pytest adds `src` to the path.
- Config comes from environment variables (names in `.env.example`; copy to `.env`, which is gitignored and may hold real credentials locally). `.env.example` does not list the login variables (`AUTH_USERS`, `JWT_SECRET`, `SESSION_MINUTES`, `COOKIE_SECURE`) yet, see `src/app/api/README.md`. Deployed secrets come from Azure Key Vault.
- Tests: `tests/unit` (CI), `tests/data_quality` (README only so far), `tests/integration` (manual / deployed env only).
- Material architectural decisions and scope changes get an ADR in `docs/adr/` (`NNNN-short-title.md`; there is no template file yet, copy the structure of `0001-continuation-runs.md`).

## Data rules (read before touching the generator or the volume)

- **New data continues the delivered plant; never generate a second new plant.** A run without `--continue` restarts all ids (`EQ-10001`, `WO-26001`, ...) over the same dates, so loaded into the same Bronze/Silver it reuses keys for different assets and overlaps downtime. `--continue` reads `src/data/raw/chain.json` (the `*_2` baseline plus every increment) and writes only work orders, maintenance history and sensor events (no equipment file, no new assets). Commit `chain.json` together with the increment files; each run is appended to it, so never generate the same window twice and upload increments in order.
- **Silver stops on ERROR rules.** The Silver notebooks abort the whole run before any write when an ERROR rule fires, so generated files must have none. Labor cost must equal `labor_hours x 55 / fixed FX` (EUR 1.0, GBP 1.17, USD 0.912; GBP/USD within 1.5 %): `SILVER_FIXED_FX` / `FX_MONTH_SPREAD` in `generate_data.py` keep it so. Assets that Silver equipment quarantines get no new activity. Verify with `src/data/generate/checks/silver_check.py`; `tests/unit/test_continuation.py` covers the same rules without Spark.
- **Never run an upload while testing.** `upload_to_databricks.py` loads the repo `.env` even when you unset the variables in the shell, and the files really land in the volume (this happened twice). Use `--dry-run`, the fake client in `tests/unit/test_upload_to_databricks.py`, or leave `--upload` out. The uploader refuses a new plant into a non-empty folder and a continuation whose volume holds runs unknown to `chain.json` (`baseline.aliases` lists other names of the baseline; `(untagged)` stands for `equipment.csv`-style names, which is how `datasets/` stores it). It does not detect a skipped increment. Nothing in the code deletes volume files, and deleting files does not remove rows already in Bronze.
- **Spark on this Windows profile only starts from an ASCII-only venv and `TEMP`** (the user profile contains Greek letters). See the docstring of `silver_check.py`.

## Repo gotchas

- `.gitignore` ignores `.github/CODEOWNERS`, so `CODEOWNERS` is not tracked. There is no `.github/workflows/` at all yet: CI/CD (§6.8) is not implemented.
- Only `.github/CODEOWNERS` (git-ignored, local) still uses the placeholder handles `@participant1`–`@participant5` (roles: data engineering, analytics, AI and API, cloud/DevOps, PM, see spec §8). The READMEs no longer name owners.
- `pyproject.toml` lists `databricks>=0.2`, an unrelated 2018 package, not the SDK; the real SDK is `databricks-sdk` in the `databricks` extra.
- `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `*.egg-info/`, `node_modules/`, `__pycache__/` and `*.log*` (including `src/data/.log/` and `src/app/agent/.log/`) exist locally and are gitignored; exclude them when searching.
- `.claude/reference/` holds the Gold notebook and its data dictionary (`gold_layer.ipynb`, `gold_layer_data_dictionary.md`) and the combined test pipeline notebook; they are reference copies, not the deployed `databricks/` code.
- `tasks/todo.md` holds the working notes of the frontend/API work.

## Problem context
Summary of what we're solving: @.claude/skills/The_task/PROBLEM.md
The full original spec is at .claude/skills/The_task/problem_statement.pdf. Consult it when
requirements are ambiguous or details are missing from the summary.
