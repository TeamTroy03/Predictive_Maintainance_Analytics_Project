# Postman collection

**Spec:** §6.7 ("Postman collection or equivalent executable API examples") · **Owner:** @participant3

**Acceptance condition:** every minimum endpoint (`GET /health`, `POST /analytics/query`,
`POST /assistant/query`, domain endpoint) has at least one success and one error example that
runs against a local or deployed instance using the `baseUrl` and `apiKey` collection variables.

Keep real API keys out of the committed collection; set them in a local Postman environment.
