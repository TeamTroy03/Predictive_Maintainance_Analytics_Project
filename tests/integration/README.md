# Integration tests

**Spec:** §6.7 (end-to-end API ↔ Gold ↔ LangChain), §6.8, §7 · **Owner:** @participant4

**Acceptance condition:** against a deployed or local environment, tests prove the API reaches the
Gold/semantic layer, the assistant executes a validated tool call end to end, auth rejects missing or
invalid credentials, and `/health` and `/ready` report correctly. Mark tests with
`@pytest.mark.integration`.
