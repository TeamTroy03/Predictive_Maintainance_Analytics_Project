"""Continuation runs of the data generator: new data extends the delivered plant without conflicts.

A small plant is generated with the real generator and registered as the baseline of a chain; the tests then
continue it and check, from the written files, the properties the Bronze/Silver layers rely on.
"""
import copy
import csv
import json
from datetime import timedelta
from pathlib import Path

import pytest

from data.generate import base_state as bs
from data.generate import generate_data as gd

BASE_TAG = "20260101_000000"
BASE_ROWS = 1000
SEED = 20260915
DAYS = 28
DATASETS = ("work_orders", "maintenance_history", "sensor_events")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def activity(directory: Path, tag: str) -> tuple[list[dict], list[dict], list[dict]]:
    return (read_csv(directory / f"work_orders_{tag}.csv"), read_csv(directory / f"maintenance_history_{tag}.csv"),
            json.loads((directory / f"sensor_events_{tag}.json").read_text(encoding="utf-8")))


def run_increment(base_dir: Path, log_dir: Path, seed: int = SEED, tag: str = "inc1", base=None,
                  days: int = DAYS) -> dict:
    """What ``generate_data.py --continue`` does for one run (without the CLI); restores the new-plant defaults."""
    base = base or bs.load_base(base_dir)
    end = base.window_end + timedelta(days=days)
    try:
        gd.set_window(base.window_end, end, end - timedelta(days=gd.CONTINUATION_BATCH_DAYS), base.id_max)
        gd.configure(max(gd.MIN_ROWS_PER_FILE, round(base.baseline_rows_per_file * days / base.baseline_days)),
                     gd.DEFAULT_DEFECT_RATE, continuation=True)
        return gd.generate(base_dir, log_dir / tag, seed, tag, base)
    finally:
        gd.set_window(*gd.DEFAULT_WINDOW)
        gd.configure(gd.DEFAULT_ROWS_PER_FILE, gd.DEFAULT_DEFECT_RATE)


@pytest.fixture(scope="module")
def plant(tmp_path_factory) -> Path:
    """A small delivered plant: a new-plant run registered as the baseline of a chain."""
    base_dir = tmp_path_factory.mktemp("plant")
    gd.set_window(*gd.DEFAULT_WINDOW)
    gd.configure(BASE_ROWS, gd.DEFAULT_DEFECT_RATE)
    try:
        gd.generate(base_dir, tmp_path_factory.mktemp("plant_log"), SEED, BASE_TAG)
    finally:
        gd.configure(gd.DEFAULT_ROWS_PER_FILE, gd.DEFAULT_DEFECT_RATE)
    (base_dir / bs.CHAIN_FILE).write_text(json.dumps({
        "baseline": {"tag": BASE_TAG, "window": [gd.iso(gd.SPAN_START), gd.iso(gd.SPAN_END)], "rows_per_file": BASE_ROWS},
        "increments": []}), encoding="utf-8")
    return base_dir


@pytest.fixture(scope="module")
def inc1(plant, tmp_path_factory):
    """First increment, written next to the baseline (not yet recorded in the chain)."""
    log = tmp_path_factory.mktemp("inc1_log")
    return run_increment(plant, log), log / "inc1"


def downtime_overlaps(base: bs.BaseState, wo_rows: list[dict]) -> int:
    """New downtime intervals that overlap delivered downtime of the same asset."""
    latest: dict[str, dict] = {}
    for r in wo_rows:
        if r["work_order_id"] not in latest or int(r["record_version"]) >= int(latest[r["work_order_id"]]["record_version"]):
            latest[r["work_order_id"]] = r
    far = bs.parse_ts("9999-01-01T00:00:00Z")
    n = 0
    for r in latest.values():
        s, e = bs.parse_ts(r["downtime_start_ts"]), bs.parse_ts(r["downtime_end_ts"])
        if s and r["equipment_id"] in base.downtime:
            n += any(s < (be or far) and bs0 < (e or far) for bs0, be in base.downtime[r["equipment_id"]])
    return n


# --- the guarantees ---------------------------------------------------------------------------------------------
def test_ids_continue_after_the_baseline(plant, inc1):
    base = bs.load_base(plant)
    wo, mh, se = activity(plant, "inc1")
    for rows, key, prefix in ((wo, "work_order_id", "WO"), (mh, "maintenance_id", "MH"), (se, "event_id", "SE")):
        numbers = sorted(bs.number(r[key]) for r in rows)
        assert numbers[0] == base.id_max[prefix] + 1, f"{prefix} must start right after the delivered maximum"
    old_wo, old_mh, old_se = activity(plant, BASE_TAG)
    assert not {r["work_order_id"] for r in wo} & {r["work_order_id"] for r in old_wo}
    assert not {r["maintenance_id"] for r in mh} & {r["maintenance_id"] for r in old_mh}
    assert not {e["event_id"] for e in se} & {e["event_id"] for e in old_se}


def test_plant_is_unchanged_no_equipment_file_and_no_new_assets(plant, inc1):
    assert not list(plant.glob("equipment_inc1*"))
    fleet = {r["equipment_id"] for r in read_csv(plant / f"equipment_{BASE_TAG}.csv") if r["equipment_id"]}
    wo, mh, se = activity(plant, "inc1")
    refs = {r["equipment_id"] for r in wo + mh} | {e["equipment_id"] for e in se}
    assert refs - fleet <= {"", None, gd.UNKNOWN_EQUIPMENT_ID}, "rows reference assets that are not in the plant"
    assert any(r["equipment_id"] in fleet for r in wo), "the increment must actually use the delivered assets"


def test_no_new_downtime_overlaps_delivered_downtime(plant, inc1):
    base = bs.load_base(plant)
    wo, _, _ = activity(plant, "inc1")
    assert downtime_overlaps(base, wo) == 0


def test_overlaps_inside_the_increment_are_exactly_the_injected_ones(plant, inc1):
    _, log = inc1
    injected = sum(1 for m in read_csv(log / "defect_manifest.csv") if m["rule"] == "overlapping_downtime")
    wo, _, _ = activity(plant, "inc1")
    by_asset: dict[str, list] = {}
    latest = {}
    for r in wo:
        if r["work_order_id"] not in latest or int(r["record_version"]) >= int(latest[r["work_order_id"]]["record_version"]):
            latest[r["work_order_id"]] = r
    for r in latest.values():
        if r["downtime_start_ts"] and r["downtime_end_ts"] and r["equipment_id"] not in ("", gd.UNKNOWN_EQUIPMENT_ID):
            by_asset.setdefault(r["equipment_id"], []).append((r["downtime_start_ts"], r["downtime_end_ts"]))
    found = 0
    for iv in by_asset.values():
        iv.sort()
        end = None
        for s, e in iv:
            found += end is not None and s < end
            end = e if end is None else max(end, e)
    assert injected > 0 and found == injected


def test_window_and_batches_follow_the_baseline(plant, inc1):
    summary, _ = inc1
    assert summary["window"] == ["2026-09-22T00:00:00Z", "2026-10-20T00:00:00Z"]
    wo, _, se = activity(plant, "inc1")
    assert {r["batch_id"] for r in wo} == {"BATCH-20261013-01", "BATCH-20261020-02"}
    assert {e["_metadata"]["batch_id"] for e in se} <= {"BATCH-20261013-01", "BATCH-20261020-02"}
    assert min(r["created_ts"] for r in wo if r["record_version"] == "1") >= "2026-09-22T00:00:00Z"


def test_same_base_and_seed_give_identical_files(plant, inc1, tmp_path_factory):
    run_increment(plant, tmp_path_factory.mktemp("again"), tag="inc1_again")
    for ds, ext in (("work_orders", "csv"), ("maintenance_history", "csv"), ("sensor_events", "json")):
        assert (plant / f"{ds}_inc1.{ext}").read_bytes() == (plant / f"{ds}_inc1_again.{ext}").read_bytes()


def test_second_increment_continues_after_the_first(plant, inc1, tmp_path_factory):
    summary, _ = inc1
    chained = tmp_path_factory.mktemp("chained")
    for f in plant.iterdir():  # work on a copy so the module-scoped plant stays pristine
        (chained / f.name).write_bytes(f.read_bytes())
    bs.append_increment(chained, {"tag": "inc1", "window": summary["window"], "rows_per_file": 1000, "seed": SEED,
                                  "id_max": summary["id_max"]})
    base = bs.load_base(chained)
    assert base.window_end == bs.parse_ts(summary["window"][1])
    assert base.id_max["WO"] >= summary["id_max"]["WO"]
    run_increment(chained, tmp_path_factory.mktemp("inc2_log"), seed=SEED + 1, tag="inc2")
    wo2, _, _ = activity(chained, "inc2")
    wo1, _, _ = activity(chained, "inc1")
    assert min(bs.number(r["work_order_id"]) for r in wo2) == max(bs.number(r["work_order_id"]) for r in wo1) + 1
    assert min(r["created_ts"] for r in wo2 if r["record_version"] == "1") >= summary["window"][1]
    assert downtime_overlaps(base, wo2) == 0, "no overlap with the first increment's downtime"


# --- the guard itself (realistic data rarely needs it, so stress it) ----------------------------------------------
def test_guard_blocks_busy_and_running_assets(plant, tmp_path_factory):
    base = bs.load_base(plant)
    until = base.window_end + timedelta(days=10)
    down_until = {eq: [(base.window_end - timedelta(days=3), until)] for eq in base.equipment}
    guarded = copy.copy(base)
    guarded.downtime = down_until
    run_increment(plant, tmp_path_factory.mktemp("g_log"), tag="stress_guarded", base=guarded)
    wo, _, _ = activity(plant, "stress_guarded")
    started = [r for r in wo if r["downtime_start_ts"] and r["equipment_id"] in down_until
               and bs.parse_ts(r["downtime_start_ts"]) < until]
    assert started == [], "downtime started while the asset was still down from an earlier run"

    unguarded = copy.copy(base)  # control: without the knowledge the same data does conflict
    unguarded.downtime = {}
    run_increment(plant, tmp_path_factory.mktemp("u_log"), tag="stress_unguarded", base=unguarded)
    wo, _, _ = activity(plant, "stress_unguarded")
    assert any(r["downtime_start_ts"] and r["equipment_id"] in down_until
               and bs.parse_ts(r["downtime_start_ts"]) < until for r in wo), "control run should show the conflict"

    running = copy.copy(base)  # downtime that never ended (no end delivered) blocks the asset for the whole window
    still_down = sorted(base.equipment)[::2]  # half of the fleet
    running.downtime = {eq: [(base.window_end - timedelta(days=1), None)] for eq in still_down}
    run_increment(plant, tmp_path_factory.mktemp("r_log"), tag="stress_running", base=running)
    wo, _, _ = activity(plant, "stress_running")
    assert [r for r in wo if r["downtime_start_ts"] and r["equipment_id"] in running.downtime] == []
    assert any(r["downtime_start_ts"] for r in wo), "the other half of the fleet must still get downtime"


def test_continuation_check_rejects_conflicting_data(plant, inc1):
    base = bs.load_base(plant)
    wo, mh, se = activity(plant, "inc1")
    as_rows = [{**r, "record_version": int(r["record_version"]), "created_ts": bs.parse_ts(r["created_ts"]),
                "downtime_start_ts": bs.parse_ts(r["downtime_start_ts"]), "downtime_end_ts": bs.parse_ts(r["downtime_end_ts"])}
               for r in wo]
    mh_rows = [{**r} for r in mh]
    eq = next(iter(base.downtime))
    s, e = base.downtime[eq][0]
    as_rows[0].update(equipment_id=eq, downtime_start_ts=s, downtime_end_ts=e or s + timedelta(hours=1))
    try:
        gd.set_window(base.window_end, base.window_end + timedelta(days=DAYS), base.window_end + timedelta(days=21),
                      base.id_max)
        with pytest.raises(AssertionError, match="overlap downtime delivered earlier"):
            gd.continuation_check(base, as_rows, mh_rows, se)
        stale = copy.copy(base)
        stale.id_max = {**base.id_max, "WO": base.id_max["WO"] + 10_000}
        with pytest.raises(AssertionError, match="not after the delivered maximum"):
            gd.continuation_check(stale, as_rows, mh_rows, se)
    finally:
        gd.set_window(*gd.DEFAULT_WINDOW)


# --- building blocks ----------------------------------------------------------------------------------------------
def test_assets_from_base_repairs_what_silver_ignores_and_drops_what_it_quarantines(plant):
    base = bs.load_base(plant)
    eq_id = min(base.equipment)
    good = dict(base.equipment[eq_id])
    repairable = {**good, "rated_speed_rpm": "0", "operating_hours_per_day": "30", "criticality": "?", "production_line": "L9"}
    quarantined = {  # each of these is quarantined by the Silver equipment notebook
        "EQ-2": {**good, "rated_power_kw": "-5"}, "EQ-3": {**good, "rated_power_kw": "5500000"},
        "EQ-4": {**good, "rated_vibration_max_mm_s": "-1"},
        "EQ-5": {**good, "install_date": "2025-01-02", "decommission_date": "2025-01-01"},
        "EQ-6": {**good, "equipment_type": "Spaceship"},
    }
    assets, _ = gd.assets_from_base({eq_id: repairable, **quarantined}, set())
    assert [a["equipment_id"] for a in assets] == [eq_id], "assets Silver quarantines must not get new activity"
    a = assets[0]
    spec = gd.EQUIPMENT_TYPES[good["equipment_type"]]
    assert a["rated_speed_rpm"] in spec["speeds"] and a["operating_hours_per_day"] in (16, 24)
    assert a["criticality"] == "C" and a["production_line"] in gd.LINES


def test_base_state_ignores_defective_equipment_keys(plant):
    base = bs.load_base(plant)
    assert "" not in base.equipment and gd.UNKNOWN_EQUIPMENT_ID not in base.equipment
    assert base.monitored <= set(base.equipment) and 0 < len(base.monitored) <= gd.N_MONITORED_ASSETS


def test_missing_chain_or_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="chain.json"):
        bs.load_base(tmp_path)
    (tmp_path / bs.CHAIN_FILE).write_text(json.dumps({"baseline": {"tag": "9", "window": [
        "2026-03-15T00:00:00Z", "2026-09-22T00:00:00Z"], "rows_per_file": 10}, "increments": []}), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="listed in chain.json but missing"):
        bs.load_base(tmp_path)


def test_set_window_resets_ids_and_fx_table():
    try:
        gd.set_window(gd.SPAN_START + timedelta(days=200), gd.SPAN_START + timedelta(days=260),
                      gd.SPAN_START + timedelta(days=253), {"WO": 40000})
        assert gd.ID_BASE["WO"] == 40001 and ("USD", "2026-11") in gd.FX_RATES
        with pytest.raises(ValueError, match="cutoff"):
            gd.set_window(gd.SPAN_START, gd.SPAN_END, gd.SPAN_END)
    finally:
        gd.set_window(*gd.DEFAULT_WINDOW)
    assert gd.ID_BASE == gd.DEFAULT_ID_BASE and ("USD", "2026-11") not in gd.FX_RATES


# --- consistency with the Silver notebook (.claude/reference/test_pipeline.ipynb) ---------------------------------
SILVER_FX = {"EUR": 1.0, "GBP": 1.17, "USD": 0.912}  # fixed rates of the Silver work-order notebook
SILVER_LABOUR_RATE = 55


def silver_labor_cost_violations(rows: list[dict]) -> tuple[int, list[str]]:
    """Silver rule LABOR_COST_CONSISTENT (an ERROR rule: it stops the whole Silver run) on closed rows, after the
    repairs Silver applies first: injected defects (sentinel 9999, negative hours, negative cost) are skipped."""
    checked, bad = 0, []
    for r in rows:
        if r["status"] != "Closed" or r["labor_hours"] == "" or r["labor_cost"] == "":
            continue
        hours, cost = float(r["labor_hours"]), float(r["labor_cost"])
        if hours < 0 or hours == 9999 or cost < 0:
            continue
        expected = round(hours * SILVER_LABOUR_RATE / SILVER_FX[r["currency"]], 2)
        tolerance = 0.01 if r["currency"] == "EUR" else abs(expected) * 0.015
        checked += 1
        if abs(cost - expected) > tolerance + 1e-9:
            bad.append(f"{r['work_order_id']} {r['currency']} hours={hours} cost={cost} expected={expected}")
    return checked, bad


def test_extended_fx_months_stay_close_to_silvers_fixed_rates():
    try:
        start = gd.SPAN_START + timedelta(days=191)
        gd.set_window(start, start + timedelta(days=3 * 365), start + timedelta(days=3 * 365 - 7))
        months = sorted({m for _, m in gd.FX_RATES} - {m for _, m in gd.DEFAULT_FX_RATES})
        assert len(months) >= 35, "the table must cover the whole window"
        for (cur, month), rate in gd.FX_RATES.items():
            if month in months:
                assert abs(rate / SILVER_FX[cur] - 1) <= 0.01, f"{cur} {month} drifted to {rate}"
    finally:
        gd.set_window(*gd.DEFAULT_WINDOW)


def test_labor_cost_passes_the_silver_rule_across_a_chain_of_increments(plant, tmp_path_factory):
    """Months far from the baseline used to drift out of Silver's 1.5 % tolerance for GBP/USD rows."""
    chained = tmp_path_factory.mktemp("labor_chain")
    for f in plant.iterdir():
        (chained / f.name).write_bytes(f.read_bytes())
    log = tmp_path_factory.mktemp("labor_chain_log")
    total = 0
    for i in range(1, 4):
        summary = run_increment(chained, log, seed=SEED + i, tag=f"c{i}")
        bs.append_increment(chained, {"tag": f"c{i}", "window": summary["window"], "rows_per_file": 1000, "seed": SEED + i,
                                      "id_max": summary["id_max"]})
        wo, _, _ = activity(chained, f"c{i}")
        checked, bad = silver_labor_cost_violations(wo)
        assert bad == [], f"increment {i}: {bad[:3]}"
        total += checked
    assert total > 300 and any(r["currency"] != "EUR" for r in wo), "the check must cover non-EUR rows too"


def test_baseline_itself_satisfies_the_labor_rule(plant):
    wo, _, _ = activity(plant, BASE_TAG)
    checked, bad = silver_labor_cost_violations(wo)
    assert checked > 300 and bad == []


def test_new_activity_only_references_assets_silver_equipment_registers(plant, inc1):
    """Silver equipment quarantines power < 0 or > 5000, negative vibration, decommission < install."""
    quarantined = set()
    for r in read_csv(plant / f"equipment_{BASE_TAG}.csv"):
        if not r["equipment_id"]:
            continue
        if (float(r["rated_power_kw"]) < 0 or float(r["rated_power_kw"]) > gd.SILVER_MAX_POWER_KW
                or float(r["rated_vibration_max_mm_s"]) < 0
                or (r["decommission_date"] and r["decommission_date"] < r["install_date"])):
            quarantined.add(r["equipment_id"])
    assert quarantined, "the baseline must contain such assets for this test to mean anything"
    wo, mh, se = activity(plant, "inc1")
    refs = {r["equipment_id"] for r in wo + mh} | {e["equipment_id"] for e in se}
    assert not refs & quarantined
