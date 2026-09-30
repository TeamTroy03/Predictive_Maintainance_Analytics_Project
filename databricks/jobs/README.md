# Databricks jobs

**Spec:** §6.1 (repeatable runs), §7 Databricks implementation ("job configuration") and
Testing and operations ("refresh/run procedure") · **Owner:** @participant1

**Acceptance condition:** a single versioned job definition runs Bronze → Silver → Gold → DQ and
reconciliation checks end to end, parameterized by environment, with run status and failures
observable. It must be triggerable live during the demo.
