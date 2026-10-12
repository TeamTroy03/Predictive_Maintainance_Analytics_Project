# Azure infrastructure

**Spec:** §6.6 Azure Fundamentals
**Deliverable:** Azure design → `docs/azure_design.md`

**Acceptance condition (§6.6 / §7):** the Azure foundation hosting the integration components is
documented and applied: subscription and Resource Group boundary, least-privilege RBAC for users,
developers and the runtime identity, justified Storage / Compute / Networking choices, secrets in
Azure Key Vault, and an environment diagram with a resource inventory (names and owners).

Infrastructure-as-code (Bicep or Terraform) or scripted CLI steps go here; record the choice in
`docs/adr/`.

## Current state

Bicep is the chosen tool. `bicep-scripts/main.bicep` is only a skeleton (resource-group scope and the `environment` and
`location` parameters, no resources yet); `dev.bicepparam` and `prod.bicepparam` set `environment`. The decision is not
recorded in `docs/adr/` yet.

