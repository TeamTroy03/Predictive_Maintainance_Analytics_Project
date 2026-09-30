# Roles

**Spec:** §8. All participants must understand the complete solution even when leading different workstreams.

| Handle | Role | Primary ownership | Repo areas |
|---|---|---|---|
| @participant1 | Data engineering | Source contracts, Bronze/Silver pipelines, audit metadata, quality, Databricks jobs | `data/`, `databricks/{common,bronze,silver,jobs}` |
| @participant2 | Analytics | Gold model, Fabric semantic model, DAX, Power BI, reconciliation | `databricks/gold/`, `fabric/` |
| @participant3 | AI and API | LangChain Core prompts/tools, structured output, FastAPI, Pydantic, API security | `app/`, `postman/` |
| @participant4 | Cloud and DevOps | Azure architecture, IAM/Key Vault, CI/CD, deployment, monitoring, integration tests | `infra/`, `.github/`, `tests/integration/` |
| @participant5 | Project manager | Plan and milestones, coordination, dependency and risk tracking, stakeholder comms, acceptance criteria, final delivery | `docs/project-management/`, `docs/decisions/` |
