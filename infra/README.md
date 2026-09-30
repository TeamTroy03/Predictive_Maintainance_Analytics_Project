# Azure infrastructure

**Spec:** §6.6 Azure Fundamentals · **Owner:** @participant4
**Deliverable:** Azure design → `docs/09-azure-design.md`

**Acceptance condition (§6.6 / §7):** the Azure foundation hosting the integration components is
documented and applied: subscription and Resource Group boundary, least-privilege RBAC for users,
developers and the runtime identity, justified Storage / Compute / Networking choices, secrets in
Azure Key Vault, and an environment diagram with a resource inventory (names and owners).

Infrastructure-as-code (Bicep or Terraform) or scripted CLI steps go here; record the choice in
`docs/decisions/`.
