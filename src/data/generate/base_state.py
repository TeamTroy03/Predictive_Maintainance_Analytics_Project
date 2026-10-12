"""Constraints for a continuation run, derived from the source files already delivered.

A continuation run (``generate_data.py --continue``) must extend the same plant: no id may be reused, no new
downtime may start while an asset is still down, and only assets that exist may be referenced. Instead of a
separate state file (which goes stale), the constraints are read from the delivered files themselves, listed in
``<base-dir>/chain.json``::

    {"baseline": {"tag": "2", "window": ["2026-03-15T00:00:00Z", "2026-09-22T00:00:00Z"], "rows_per_file": 10000},
     "increments": [{"tag": "20261020_101500", "window": [...], "rows_per_file": 1466, "seed": 1, "id_max": {...}}]}

``tag`` is the file suffix: ``<dataset>_<tag>.<ext>``. The baseline has all four datasets (``equipment`` is the
fixed reference data of the plant); increments only have ``work_orders``, ``maintenance_history`` and
``sensor_events``. Standard library only.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CHAIN_FILE = "chain.json"
UNKNOWN_EQUIPMENT_ID = "EQ-9999"
EXT = {"equipment": "csv", "work_orders": "csv", "maintenance_history": "csv", "sensor_events": "json"}
ACTIVITY_DATASETS = ("work_orders", "maintenance_history", "sensor_events")


def parse_ts(value: str | None) -> datetime | None:
    """``2026-09-21T21:49:27Z`` -> aware UTC datetime (``None`` for empty)."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if value else None


def number(key: str | None) -> int:
    """Numeric suffix of an id such as ``WO-26001`` or ``SE-000001`` (0 if there is none)."""
    m = re.search(r"(\d+)$", key or "")
    return int(m.group(1)) if m else 0


def chain_path(base_dir: Path) -> Path:
    return Path(base_dir) / CHAIN_FILE


def load_chain(base_dir: Path) -> dict:
    """Read ``chain.json``.

    Raises:
        FileNotFoundError: if the chain file is missing (nothing to continue from).
    """
    path = chain_path(base_dir)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: it lists the delivered files this run continues from")
    return json.loads(path.read_text(encoding="utf-8"))


def file_for(base_dir: Path, dataset: str, tag: str) -> Path:
    return Path(base_dir) / f"{dataset}_{tag}.{EXT[dataset]}"


@dataclass
class BaseState:
    """Everything a continuation run needs to know about the delivered data."""
    chain: dict
    window_end: datetime                        # the new window starts here
    baseline_days: float                        # length of the baseline window
    baseline_rows_per_file: int
    equipment: dict[str, dict]                  # latest row per valid equipment_id
    monitored: set[str]                         # equipment_ids that send sensor events
    id_max: dict[str, int]                      # highest issued number per prefix: EQ, WO, MH, SE
    downtime: dict[str, list[tuple[datetime, datetime | None]]] = field(default_factory=dict)
    # downtime[equipment_id] = [(start, end)], end None = still running (no end delivered yet)

    def busy_until(self, equipment_id: str) -> datetime | None:
        """End of the asset's last known downtime; ``None`` if it never had one."""
        ends = [e for _, e in self.downtime.get(equipment_id, []) if e is not None]
        return max(ends) if ends else None

    def running(self) -> set[str]:
        """Assets whose latest downtime has no end yet (still down)."""
        return {eq for eq, iv in self.downtime.items() if any(e is None for _, e in iv)}


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _latest(rows: list[dict], key: str) -> dict[str, dict]:
    """Highest ``record_version`` per key (later rows win ties)."""
    out: dict[str, dict] = {}
    for r in rows:
        k = r.get(key)
        if k and (k not in out or int(r["record_version"] or 0) >= int(out[k]["record_version"] or 0)):
            out[k] = r
    return out


def load_base(base_dir: Path) -> BaseState:
    """Read the baseline and every increment listed in the chain.

    Raises:
        FileNotFoundError: if the chain or one of the files it lists is missing.
    """
    base_dir = Path(base_dir)
    chain = load_chain(base_dir)
    baseline = chain["baseline"]
    tags = [baseline["tag"]] + [i["tag"] for i in chain.get("increments", [])]
    for tag in tags:
        datasets = EXT if tag == baseline["tag"] else ACTIVITY_DATASETS
        for ds in datasets:
            if not file_for(base_dir, ds, tag).is_file():
                raise FileNotFoundError(f"{file_for(base_dir, ds, tag)} is listed in {CHAIN_FILE} but missing: "
                                        f"restore it, or remove its entry from {CHAIN_FILE}")

    equipment = _latest(_read_csv(file_for(base_dir, "equipment", baseline["tag"])), "equipment_id")
    equipment.pop(UNKNOWN_EQUIPMENT_ID, None)

    wo_rows = [r for t in tags for r in _read_csv(file_for(base_dir, "work_orders", t))]
    mh_rows = [r for t in tags for r in _read_csv(file_for(base_dir, "maintenance_history", t))]
    events = [e for t in tags for e in json.loads(file_for(base_dir, "sensor_events", t).read_text(encoding="utf-8"))]

    # Work orders whose own equipment_id is damaged (null / EQ-9999) are attributed via their maintenance rows.
    mh_equipment = {r["work_order_id"]: r["equipment_id"] for r in mh_rows
                    if r["work_order_id"] and r["equipment_id"] not in ("", UNKNOWN_EQUIPMENT_ID)}
    downtime: dict[str, list[tuple[datetime, datetime | None]]] = {}
    for r in _latest(wo_rows, "work_order_id").values():
        eq = r["equipment_id"]
        if eq in ("", UNKNOWN_EQUIPMENT_ID):
            eq = mh_equipment.get(r["work_order_id"], "")
        if eq and r["downtime_start_ts"]:
            downtime.setdefault(eq, []).append((parse_ts(r["downtime_start_ts"]), parse_ts(r["downtime_end_ts"])))

    monitored = {e["equipment_id"] for e in events if e.get("equipment_id") in equipment}
    id_max = {"EQ": max(number(k) for k in equipment),
              "WO": max(number(r["work_order_id"]) for r in wo_rows),
              "MH": max(number(r["maintenance_id"]) for r in mh_rows),
              "SE": max(number(e["event_id"]) for e in events)}
    for inc in chain.get("increments", []):  # also honour what an increment recorded about itself
        for prefix, value in inc.get("id_max", {}).items():
            id_max[prefix] = max(id_max.get(prefix, 0), value)

    start, end = (parse_ts(x) for x in baseline["window"])
    last_window = chain["increments"][-1]["window"] if chain.get("increments") else baseline["window"]
    return BaseState(chain=chain, window_end=parse_ts(last_window[1]), baseline_days=(end - start).total_seconds() / 86400,
                     baseline_rows_per_file=baseline["rows_per_file"], equipment=equipment, monitored=monitored,
                     id_max=id_max, downtime=downtime)


def append_increment(base_dir: Path, entry: dict) -> None:
    """Record a finished increment so the next ``--continue`` starts after it."""
    chain = load_chain(base_dir)
    chain.setdefault("increments", []).append(entry)
    chain_path(base_dir).write_text(json.dumps(chain, indent=2) + "\n", encoding="utf-8")
