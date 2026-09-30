# Refresh strategy

**Spec:** §4 "A documented refresh strategy, including the treatment of late-arriving and corrected
records"; §6.1 incremental/idempotent loads; §6.2 SCD and late-arriving events.

## Load pattern per source
| Source | Incremental or full refresh | Watermark / change detection | Justification |
|---|---|---|---|
| | | | |

## Late-arriving sensor events
_TODO_

## Corrected records
_TODO_

## Slowly changing equipment attributes
_TODO: SCD type chosen for `dim_equipment` and why._

## Rerun of a failed batch
_TODO: how idempotency is guaranteed (record hash, MERGE, batch ID)._
