# Common utilities

**Spec:** §6.1 (audit metadata, logging), used by §6.2–6.3

**Acceptance condition:** every layer uses the same implementation for ingestion timestamp,
batch ID, source filename, source-system key, record hash, load status and run metrics
(input / written / rejected counts, duration, failure details), so lineage columns are
consistent end to end.

Also holds environment-based configuration (catalog, schema and path names per `dev`/`prod`);
no secrets are stored here.
