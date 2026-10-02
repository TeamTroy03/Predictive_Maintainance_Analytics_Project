# 10 — CI/CD pipeline

**Spec:** §7 Deliverable "CI/CD pipeline"; §6.8 · **Owner:** @participant4

**Required evidence:** versioned YAML with triggers, lint, tests, build, secrets, environments,
approvals and deployment stage.

## Pipeline
Definition: `.github/workflows/ci.yml`.

| Stage | Trigger | Gate |
|---|---|---|
| lint | PR, push to main | ruff |
| test | after lint | pytest (excluding integration) |
| build | after test | Docker image artifact |
| deploy-dev | push to main, after build | `dev` environment approval |

## Secrets
_TODO: which GitHub secrets exist and where they are used; nothing committed._

## Environments and approvals
_TODO_

## Configuration promotion (dev → prod)
_TODO_

## Rollback
_TODO_

## Evidence required before approval
_TODO_
