# Problem summary

Source: `problem_statement.pdf` (same folder; also `context/`) (common spec for the AI and Data Engineering capstone; 5 participants per team). This repo is **Team 3 — Predictive Maintenance Analytics**. Section numbers (§) refer to the PDF.

## Goals

**Business problem (§2):** operational data is spread over CSV/JSON extracts with no ingestion contract or lineage, contains unchecked quality defects, and has no shared semantic layer for KPIs. The result is manual prep, untrusted metrics and no way to reproduce how a reported value was produced.

**Team 3 case:** an industrial company needs to understand equipment health, abnormal sensor behavior, maintenance effectiveness, downtime and recurring failure modes.

**Objective (§3):** a production-minded, end-to-end solution that turns heterogeneous source data into trusted, explainable, decision-ready information:
`CSV/JSON → Databricks Bronze → Silver → Gold (Delta star schema) → Fabric semantic model → Power BI report`, plus secured FastAPI endpoints and a LangChain Core assistant that can call only approved data tools, hosted on Azure and delivered by CI/CD.

## Requirements

**Dataset (§4)**
- ≥ 2 related transactional CSVs, ≥ 1 reference CSV, ≥ 1 semi-structured JSON; ≥ 10,000 transactional/event records in total.
- Synthetic, internally consistent, no real personal or confidential data; small enough to refresh live in the demo.
- Audit/lineage metadata: source filename, ingestion timestamp, batch ID, source-system key.
- Also required: data dictionary, source-to-target mapping for all Silver and Gold tables, deliberately injected defects, documented refresh strategy (including late-arriving and corrected records).

**Team 3 specifics**

| Element | Value |
|---|---|
| Sources | `equipment.csv`, `maintenance_history.csv`, `work_orders.csv`, `sensor_events.json` |
| Grain | one sensor reading at a timestamp; one maintenance work order |
| Facts | `fact_sensor_reading`, `fact_maintenance_work_order`, `fact_downtime` |
| Dimensions | `dim_date`, `dim_equipment`, `dim_site`, `dim_equipment_type`, `dim_failure_mode`, `dim_maintenance_type` |
| KPIs | asset availability, unplanned downtime, MTBF, MTTR, overdue work orders, alarm rate, maintenance cost, repeat-failure rate |
| Report pages | fleet health, sensor trends, maintenance performance, equipment drill-through, critical exceptions |
| Priority DQ rules | out-of-range readings, unit inconsistency, missing equipment keys, overlapping downtime, closure before creation, duplicate sensor events |

**Scope of work (§6, all mandatory)**
- **6.1 Bronze:** parameterized ingestion for CSV and JSON; raw Delta, no business transforms; add ingestion timestamp, batch ID, source filename, record hash and load status; schema validation with a controlled response to drift; idempotent reruns (incremental, or justified full refresh); log input/written/rejected counts, duration and failures. *Acceptance:* trace any record to its source file and batch; rerun a failed batch without uncontrolled duplicates.
- **6.2 Silver:** standardize names, types, dates, time zones, codes, currencies and units; apply domain rules; deduplicate; resolve invalid or missing keys; flatten JSON and join reference data; DQ expectations for completeness, validity, uniqueness and referential integrity; quarantine table with reason, rule and timestamp; document slowly-changing attributes and late-arriving events. *Acceptance:* consistent data, measurable quality, visible and actionable rejects.
- **6.3 Gold:** Delta star schema with the grain of every fact stated; ≥ 1 central fact and ≥ 4 dimensions including a date dimension; surrogate keys and documented unknown-member handling; business measures, status logic and aggregates; optimized Delta layout with a short performance rationale; Silver→Gold reconciliation (row counts and critical totals); table and column descriptions. *Acceptance:* measures reproducible from facts and dimensions without logic embedded in visuals.
- **6.4 Fabric / Power BI:** connect using the access pattern agreed with the instructor; relationships, hierarchies, formats, descriptions and a dedicated measures table; ≥ 8 explicit DAX measures (volume, rate/ratio, trend, exception); proper date table with validated filter direction; row-level security if the case has ownership boundaries; ≥ 3 report pages (executive overview, analytical detail, exceptions/drill-through) with filters, tooltips, accessible titles, clear units and a visible last-refresh indicator; report totals validated against Gold.
- **6.5 LangChain Core (`langchain-core`):** correct use of System/Human/AI/Tool messages; prompt templates (business context, evidence, response format, limitations); ≥ 2 typed tools bound to the chat model (one KPI/aggregate, one filtered detail/metadata); detect tool calls → validate arguments → execute → return `ToolMessage`; structured output separating answer, evidence, filters, source/measure, limitations; handle malformed arguments, unavailable data, tool errors and out-of-scope requests. *Acceptance:* a question triggers a validated tool call grounded in Gold/semantic data with a traceable execution path.
- **6.6 Azure:** document and apply the foundation: account/subscription/Resource Group boundary; least-privilege RBAC for users, developers and the runtime identity; justified Storage, Compute and Networking; secrets in Key Vault for the deployed solution; environment diagram and resource inventory.
- **6.7 REST / FastAPI:** REST fundamentals (resources, verbs, status codes, consistent errors); Pydantic request/response models, validation and exception handling; OpenAPI docs plus a Postman collection; for external calls, timeouts, pagination, bounded retries and rate-limit handling; API key in dev and OAuth2 bearer/JWT in the deployed design; health and readiness endpoints, correlation IDs, structured logs with no secrets; end-to-end connection to Gold/semantic layer and LangChain tools (mock cloud dependencies only when clearly documented). *Minimum API:* `GET /health`, `POST /analytics/query`, `POST /assistant/query`, and one domain endpoint.
- **6.8 CI/CD (GitHub Actions or Azure DevOps):** versioned pipeline triggered on PRs and `main`; separate lint, test, build and deploy jobs with clear dependencies; API (and container image) built as a reproducible artifact; secrets from repo/pipeline stores only; environments, approvals and staged deployment; documented rollback, config promotion and pre-approval evidence. *Acceptance:* a source change triggers the pipeline, gates run, an artifact is built, and deployment completes or waits for approval.

**Deliverables (§7):** business and data brief; architecture and lineage diagram (Databricks, Fabric, FastAPI, LangChain, Azure); Databricks implementation (notebooks/modules, job config, Delta tables); quality evidence (rules, test results, quarantine examples, reconciliation summary); dimensional model docs; Fabric/Power BI; LangChain integration; FastAPI integration; Azure design; CI/CD YAML; testing and operations (tests, monitoring/log evidence, refresh procedure, known limitations); final presentation (15-minute demo + 5-minute Q&A, every participant contributes).

**Evaluation (§9):** Business framing/KPIs 8%, Bronze 12%, Silver 15%, Gold 15%, Fabric/report 15%, LangChain 10%, FastAPI/Azure/security 10%, CI/CD and operations 8%, demo and teamwork 7%. Bonus (5%): a simple Knowledge Graph for the domain with one graph query exposed through the AI assistant.

## Constraints

- Optional enhancements come only after the full baseline works end to end.
- Cross-cutting: environment-based config, secrets outside source control, RBAC, source-to-report lineage, job monitoring, automated tests, version control, reproducible deployment.
- The LLM must not bypass the semantic model, fabricate data or take consequential business actions.
- Never commit tokens, passwords, connection strings or model credentials.
- Dataset must stay small enough to refresh live during the demo.
- Fabric access pattern to Gold must be agreed with the instructor.
- Process: short iterations, shared backlog (owner, priority, acceptance criteria, status), peer review via PRs, architectural decisions and scope changes recorded in the repo, risk log (data availability, access, quality, integration, schedule).
- Each checkpoint needs a working increment; slides alone do not count as completion. A polished report cannot compensate for an incomplete pipeline, and a correct pipeline still needs understandable business measures.
- Roles (§8): P1 data engineering; P2 analytics; P3 AI and API; P4 cloud and DevOps; P5 project manager. Everyone must understand the whole solution.

## Glossary

| Term | Meaning |
|---|---|
| Bronze / Silver / Gold | Medallion layers: raw append-only → validated and conformed → business-ready star schema |
| Delta (Lake) | Table format in Databricks giving reliable storage and table history |
| Grain | What one row of a fact table represents |
| Star schema / conformed dimension | Facts joined to dimensions; a conformed dimension is shared across facts |
| Surrogate key / unknown member | Synthetic dimension key / placeholder row for facts whose dimension key is missing or invalid |
| Quarantine | Table holding rejected records with reason, rule and timestamp |
| Schema drift | Source structure changing from the expected schema |
| Idempotent rerun | Re-running a batch without creating duplicates |
| Record hash | Hash of a row's content used for lineage and change/duplicate detection |
| Late-arriving event | Record that arrives after its period was already processed |
| Reconciliation | Comparing row counts and totals between layers (e.g. Silver vs Gold) |
| Semantic model | Fabric layer with relationships, hierarchies and governed DAX measures |
| DAX | Measure language of Power BI / Fabric semantic models |
| RLS | Row-level security restricting data by user or role |
| OneLake / Lakehouse / Warehouse | Fabric storage and access options for the semantic model |
| LangChain Core | `langchain-core` primitives: messages, prompt templates, tool binding and calling |
| ToolMessage | Message carrying a tool's result back to the model |
| Structured output | Typed response separating answer, evidence, filters, source/measure and limitations |
| Correlation ID | Per-request identifier used to trace logs across components |
| Key Vault | Azure service for secrets and credentials |
| RBAC / least privilege | Role-based access granting only the permissions needed |
| MTBF / MTTR | Mean time between failures / mean time to repair |
| Availability | Share of time equipment is operational |
| Unplanned downtime | Downtime from unscheduled failures, as opposed to planned maintenance |
| Alarm rate | Frequency of sensor readings breaching alarm limits |
| Repeat-failure rate | Share of failures recurring on the same equipment or failure mode |
| Work order | Maintenance task with created, due and closed timestamps, type and status |
| Failure mode | Category describing how equipment failed |
