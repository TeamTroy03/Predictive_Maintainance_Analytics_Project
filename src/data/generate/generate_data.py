#!/usr/bin/env python3
"""Synthetic source-data generator for Team 3 - Predictive Maintenance Analytics.

Generates the four extended source files described in ``data_dictionary.md``:

* ``equipment_2.csv``           asset master data, one row per asset version
* ``work_orders_2.csv``         work orders, one row per work-order version
* ``maintenance_history_2.csv`` maintenance activities, one row per activity version
* ``sensor_events_2.json``      IoT readings, JSON array, one reading per event

Guarantees
----------
* Every file has exactly ``ROWS_PER_FILE`` rows (10,000).
* Exactly ``DEFECT_RATE`` (5 %) of the rows of every file carry ONE injected
  defect from the priority data-quality rules (500 rows per file, split evenly
  over five rules per file). Every injected defect is listed in
  ``defect_manifest.csv`` (file, row index, key, rule, detail) so Silver
  detection can be validated against ground truth.
* Outside the manifest the data is clean: no accidental overlaps, orphans,
  inverted timestamps or duplicates. ``self_check`` re-derives the defect
  counts from the written files and fails loudly if they do not match.
* Deterministic: the same seed always produces byte-identical files.

Physics (summary, see README notes printed at the end)
------------------------------------------------------
* Readings depend on load (shift pattern), ambient temperature (season + day)
  and noise. Units are SI: mm/s, degC, bar, A, rpm, %.
* Unplanned failures on IoT-monitored assets are preceded by 7-45 days of
  failure-mode-specific degradation (e.g. bearing wear: vibration first,
  bearing temperature later). Repairs and PM reset the degradation.
* During downtime the asset is ``stopped``/``maintenance``: speed and current
  are 0 and temperatures decay towards ambient.

Usage
-----
    python generate_pdm_data.py --out ./data --seed 20260915

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc

# =============================================================================
# 1. Configuration
# =============================================================================
SEED = 20260915
ROWS_PER_FILE = 10_000
DEFECT_RATE = 0.05
N_DEFECTS = round(ROWS_PER_FILE * DEFECT_RATE)  # 500 per file

SPAN_START = datetime(2026, 3, 15, tzinfo=UTC)  # first operational timestamp
SPAN_END = datetime(2026, 9, 22, tzinfo=UTC)    # exclusive; end of batch-2 window
CUTOFF = datetime(2026, 9, 15, tzinfo=UTC)      # batch 1 = [.., CUTOFF), batch 2 = [CUTOFF, SPAN_END)

BATCHES = {
    1: {"batch_id": "BATCH-20260915-01", "ingestion_ts": datetime(2026, 9, 15, 2, 0, tzinfo=UTC), "stamp": "20260915"},
    2: {"batch_id": "BATCH-20260922-02", "ingestion_ts": datetime(2026, 9, 22, 2, 0, tzinfo=UTC), "stamp": "20260922"},
}

UNKNOWN_EQUIPMENT_ID = "EQ-9999"  # never issued; asset IDs start at EQ-10001
N_MONITORED_ASSETS = 20           # assets with IoT sensors (feed sensor_events)
N_LEGACY_FIRMWARE_MONITORED = 5   # monitored assets behind gateways that send imperial units
N_EQUIPMENT_VERSIONS = 150        # SCD type-2 changes (record_version 2)
N_LEGACY_ACTIVITIES = 50          # maintenance rows from LEGACY_CMMS without work order
N_MH_CORRECTIONS = 100            # corrected maintenance rows (record_version 2)
N_WO_CLOSURE_FIXES = 30           # WO v2 rows that correct a closure-before-creation defect
N_WO_PRIORITY_FIXES = 30          # WO v2 rows that correct a mis-keyed priority
N_SENSOR_CORRECTIONS = 30         # sensor v2 events that correct an out-of-range spike
N_LATE_EVENTS = 150               # sensor events delivered late (in batch 2)
WO_CANDIDATES = 10_600            # generated before trimming to exactly 10,000 rows

LABOR_RATE_EUR = 55.0
SLA_HOURS = {"P1": 24, "P2": 72, "P3": 168, "P4": 336}
PM_INTERVAL_DAYS = {"A": 30, "B": 60, "C": 90}

EQUIPMENT_RULES = ["missing_equipment_key", "out_of_range_value", "unit_inconsistency",
                   "closure_before_creation", "duplicate_record"]
WORK_ORDER_RULES = ["missing_equipment_key", "overlapping_downtime", "closure_before_creation",
                    "out_of_range_value", "duplicate_record"]
MAINTENANCE_RULES = ["missing_equipment_key", "closure_before_creation", "out_of_range_value",
                     "unit_inconsistency", "duplicate_record"]
SENSOR_RULES = ["out_of_range_reading", "unit_inconsistency", "missing_equipment_key",
                "duplicate_sensor_event_exact", "duplicate_sensor_event_near"]

EXPECTED_HANDLING = {
    "missing_equipment_key": "Resolve via related record (sensor_id prefix / work order / activity) if possible, "
                             "else map to Unknown member (-1) and quarantine",
    "out_of_range_value": "Quarantine with rule and reason; exclude from KPIs",
    "out_of_range_reading": "Quarantine reading; exclude from KPIs (a later corrected version may supersede it)",
    "unit_inconsistency": "Convert to canonical SI unit in Silver and flag as standardized",
    "closure_before_creation": "Quarantine, unless a later record_version corrects it (keep latest)",
    "overlapping_downtime": "Merge/trim overlapping intervals per asset before fact_downtime",
    "duplicate_record": "Deduplicate: keep one row per business key and record_version",
    "duplicate_sensor_event_exact": "Drop exact duplicate (same event_id, version and payload)",
    "duplicate_sensor_event_near": "Deduplicate on equipment_id + sensor_id + event_ts; keep earliest received_ts",
}

# =============================================================================
# 2. Domain reference data (fictional)
# =============================================================================
SITES = {
    "PLT-A": {"site_name": "Rheinfeld Works", "country": "Germany", "site_timezone": "Europe/Berlin",
              "ambient_offset_c": 0.0},
    "PLT-B": {"site_name": "Valmora Plant", "country": "Spain", "site_timezone": "Europe/Madrid",
              "ambient_offset_c": 3.0},
}
LINES = ["L1", "L2", "L3", "L4"]
CURRENT_FIRMWARE, LEGACY_FIRMWARE = "4.2.1", "3.9.7"  # 3.9.7 sends degF / psi / in/s
GATEWAY_FIRMWARE = {(s, l): CURRENT_FIRMWARE for s in SITES for l in LINES}
GATEWAY_FIRMWARE[("PLT-B", "L1")] = LEGACY_FIRMWARE
GATEWAY_FIRMWARE[("PLT-B", "L2")] = LEGACY_FIRMWARE

MANUFACTURERS = {"KDY": "Kestrel Dynamics", "NWM": "Nordwerk Maschinenbau", "AVF": "Avalon Fluidtech",
                 "TSI": "Tessaro Industrie", "BLM": "Brightline Motors", "HGR": "Hollis & Grange",
                 "ORV": "Orvane Systems"}
IEC_POWER_KW = [5.5, 7.5, 11, 15, 18.5, 22, 30, 37, 45, 55, 75, 90, 110, 132, 160, 200, 250, 315,
                355, 400, 450, 500, 560, 630, 710, 800, 900, 1000, 1120, 1200]

MEASURES = {
    "vibration_rms": {"family": "vibration", "unit": "mm/s", "code": "VIB", "alarm_code": "VIB_HIGH", "dp": 2},
    "bearing_temperature": {"family": "temperature", "unit": "degC", "code": "TMP", "alarm_code": "TMP_HIGH", "dp": 1},
    "oil_temperature": {"family": "temperature", "unit": "degC", "code": "TMP", "alarm_code": "TMP_HIGH", "dp": 1},
    "winding_temperature": {"family": "temperature", "unit": "degC", "code": "TMP", "alarm_code": "TMP_HIGH", "dp": 1},
    "discharge_pressure": {"family": "pressure", "unit": "bar", "code": "PRS", "alarm_code": "PRS_HIGH", "dp": 2},
    "motor_current": {"family": "current", "unit": "A", "code": "CUR", "alarm_code": "CUR_HIGH", "dp": 1},
    "shaft_speed": {"family": "speed", "unit": "rpm", "code": "SPD", "alarm_code": None, "dp": 0},
    "load_pct": {"family": "load", "unit": "%", "code": "LOD", "alarm_code": None, "dp": 1},
}
MEASUREMENT_POINT = {"vibration_rms": "DE-BEARING", "bearing_temperature": "DE-BEARING",
                     "oil_temperature": "OIL-SUMP", "winding_temperature": "WINDING",
                     "discharge_pressure": "DISCHARGE", "motor_current": "MOTOR-TERMINAL",
                     "shaft_speed": "SHAFT", "load_pct": "OUTPUT-SHAFT"}
POINT_TAG = {"DE-BEARING": "drive-end", "OIL-SUMP": "oil-sump", "WINDING": "stator-winding",
             "DISCHARGE": "discharge", "MOTOR-TERMINAL": "motor-terminal", "SHAFT": "shaft",
             "OUTPUT-SHAFT": "output-shaft"}

# temp_base / vib_base: steady running value at 75 % load and 20 degC ambient.
# pressure: (alarm_high = rated max bar, normal running bar) or None.
_PUMP_MEASURES = ["vibration_rms", "bearing_temperature", "discharge_pressure", "motor_current"]
_MOTOR_MEASURES = ["vibration_rms", "winding_temperature", "motor_current", "shaft_speed"]
EQUIPMENT_TYPES: dict[str, dict[str, Any]] = {
    "Centrifugal Pump": dict(prefix="P", weight=.18, power=(11, 160), speeds=[1480, 2960],
                             temp_measure="bearing_temperature", temp_alarm=95, temp_base=62,
                             vib_alarm=7.1, vib_base=2.2, pressure=(16.0, 7.5), measures=_PUMP_MEASURES,
                             failure_modes=["BRG-WEAR", "SEAL-LEAK", "CAVIT", "MISALIGN", "IMBAL", "CLOG"],
                             manufacturers=["AVF", "KDY", "TSI"], crit_w=(.25, .45, .30), indoor=True),
    "Boiler Feed Pump": dict(prefix="BFP", weight=.05, power=(250, 1200), speeds=[2980],
                             temp_measure="bearing_temperature", temp_alarm=95, temp_base=66,
                             vib_alarm=7.1, vib_base=2.4, pressure=(200.0, 168.0), measures=_PUMP_MEASURES,
                             failure_modes=["BRG-WEAR", "SEAL-LEAK", "CAVIT", "MISALIGN"],
                             manufacturers=["AVF", "KDY"], crit_w=(.60, .35, .05), indoor=True),
    "Air Compressor": dict(prefix="AC", weight=.12, power=(37, 250), speeds=[1480, 2960],
                           temp_measure="oil_temperature", temp_alarm=110, temp_base=80,
                           vib_alarm=6.3, vib_base=2.0, pressure=(13.0, 8.5),
                           measures=["vibration_rms", "oil_temperature", "discharge_pressure", "motor_current"],
                           failure_modes=["BRG-WEAR", "VALVE-FAIL", "OVERHEAT", "LUB-FAIL"],
                           manufacturers=["ORV", "NWM"], crit_w=(.30, .45, .25), indoor=True),
    "Conveyor Drive Motor": dict(prefix="CDM", weight=.14, power=(5.5, 75), speeds=[1460],
                                 temp_measure="winding_temperature", temp_alarm=155, temp_base=95,
                                 vib_alarm=4.5, vib_base=1.4, pressure=None, measures=_MOTOR_MEASURES,
                                 failure_modes=["BRG-WEAR", "WIND-INS", "OVERHEAT", "MISALIGN"],
                                 manufacturers=["BLM", "HGR"], crit_w=(.15, .45, .40), indoor=True),
    "Hydraulic Press": dict(prefix="HP", weight=.07, power=(30, 250), speeds=[1480],
                            temp_measure="oil_temperature", temp_alarm=70, temp_base=42,
                            vib_alarm=4.5, vib_base=1.3, pressure=(315.0, 220.0),
                            measures=["vibration_rms", "oil_temperature", "discharge_pressure", "motor_current"],
                            failure_modes=["SEAL-LEAK", "OVERHEAT", "LUB-FAIL", "VALVE-FAIL"],
                            manufacturers=["NWM", "TSI"], crit_w=(.35, .45, .20), indoor=True),
    "CNC Spindle": dict(prefix="CNC", weight=.10, power=(7.5, 45), speeds=[12000, 18000, 24000],
                        temp_measure="bearing_temperature", temp_alarm=80, temp_base=46,
                        vib_alarm=2.8, vib_base=0.8, pressure=None,
                        measures=["vibration_rms", "bearing_temperature", "motor_current", "shaft_speed"],
                        failure_modes=["BRG-WEAR", "IMBAL", "LUB-FAIL", "OVERHEAT"],
                        manufacturers=["TSI", "NWM"], crit_w=(.30, .50, .20), indoor=True),
    "Induction Motor": dict(prefix="M", weight=.18, power=(5.5, 315), speeds=[990, 1480, 2960],
                            temp_measure="winding_temperature", temp_alarm=155, temp_base=92,
                            vib_alarm=4.5, vib_base=1.5, pressure=None, measures=_MOTOR_MEASURES,
                            failure_modes=["BRG-WEAR", "WIND-INS", "IMBAL", "MISALIGN"],
                            manufacturers=["BLM", "HGR", "KDY"], crit_w=(.20, .45, .35), indoor=True),
    "Gearbox": dict(prefix="GB", weight=.09, power=(15, 250), speeds=[1480],
                    temp_measure="oil_temperature", temp_alarm=95, temp_base=64,
                    vib_alarm=7.1, vib_base=2.3, pressure=None,
                    measures=["vibration_rms", "oil_temperature", "load_pct"],
                    failure_modes=["GEAR-WEAR", "BRG-WEAR", "LUB-FAIL", "MISALIGN"],
                    manufacturers=["HGR", "NWM"], crit_w=(.25, .45, .30), indoor=True),
    "Cooling Tower Fan": dict(prefix="CTF", weight=.07, power=(11, 90), speeds=[1480],
                              temp_measure="bearing_temperature", temp_alarm=90, temp_base=52,
                              vib_alarm=7.1, vib_base=2.6, pressure=None, measures=_MOTOR_MEASURES[:1] +
                              ["bearing_temperature", "motor_current", "shaft_speed"],
                              failure_modes=["IMBAL", "BRG-WEAR", "MISALIGN", "GEAR-WEAR"],
                              manufacturers=["ORV", "HGR"], crit_w=(.20, .45, .35), indoor=False),
}
VIB_WARNING = {7.1: 4.5, 6.3: 4.0, 4.5: 2.8, 2.8: 1.8}  # ISO 10816-style zone boundaries

# code: (description, ISO 14224-style failure mode, failure mechanism)
FAILURE_MODES = {
    "BRG-WEAR": ("Bearing wear", "VIB", "Mechanical failure - wear"),
    "SEAL-LEAK": ("Mechanical seal leakage", "ELP", "Mechanical failure - leakage"),
    "CAVIT": ("Cavitation", "PDE", "Material failure - cavitation"),
    "MISALIGN": ("Shaft misalignment", "VIB", "Mechanical failure - clearance/alignment"),
    "IMBAL": ("Rotor imbalance", "VIB", "Mechanical failure - vibration"),
    "LUB-FAIL": ("Lubrication failure", "OHE", "Mechanical failure - overheating"),
    "WIND-INS": ("Stator winding insulation breakdown", "BRD", "Electrical failure - short circuiting"),
    "OVERHEAT": ("Overheating", "OHE", "Mechanical failure - overheating"),
    "GEAR-WEAR": ("Gear tooth wear", "NOI", "Mechanical failure - wear"),
    "VALVE-FAIL": ("Valve failure", "LOO", "Mechanical failure - breakage"),
    "CLOG": ("Blocked suction / plugged strainer", "PLU", "Other - blockage"),
    "NONE": ("No failure (planned work)", None, None),
}
FM_SYMPTOM = {
    "BRG-WEAR": "high vibration and noise at bearing", "SEAL-LEAK": "process fluid leaking at seal",
    "CAVIT": "crackling noise and unstable discharge pressure", "MISALIGN": "high axial vibration, coupling hot",
    "IMBAL": "high 1x radial vibration", "LUB-FAIL": "rising oil and bearing temperature",
    "WIND-INS": "high motor current at normal load", "OVERHEAT": "operating temperature above normal",
    "GEAR-WEAR": "gear mesh noise and rising vibration", "VALVE-FAIL": "loss of output pressure",
    "CLOG": "high discharge pressure and reduced flow",
}
# Degradation signature per failure mode: family -> (shape, weight).
# 'lead' rises from onset; 'lag' starts at 40 % of the degradation period.
FM_EFFECTS = {
    "BRG-WEAR": {"vib": ("lead", 1.0), "temp": ("lag", 0.8)},
    "SEAL-LEAK": {"pres_drop": ("lead", 1.0), "temp": ("lag", 0.4)},
    "CAVIT": {"vib": ("lead", 0.8), "pres_drop": ("lead", 0.6)},
    "MISALIGN": {"vib": ("lead", 1.0), "temp": ("lag", 0.5)},
    "IMBAL": {"vib": ("lead", 1.0)},
    "LUB-FAIL": {"temp": ("lead", 1.0), "vib": ("lag", 0.5)},
    "WIND-INS": {"current": ("lead", 1.0), "temp": ("lag", 0.8)},
    "OVERHEAT": {"temp": ("lead", 1.0), "current": ("lag", 0.3)},
    "GEAR-WEAR": {"vib": ("lead", 1.0), "temp": ("lag", 0.6)},
    "VALVE-FAIL": {"pres_drop": ("lead", 0.8), "temp": ("lag", 0.6)},
    "CLOG": {"pres_up": ("lead", 1.0), "current": ("lag", 0.4)},
}
ROOT_CAUSES = {
    "RC-WEAR": ("Normal wear-out / end of life", "Physical"),
    "RC-LUB": ("Inadequate lubrication", "Maintenance"),
    "RC-INST": ("Installation or alignment error", "Maintenance"),
    "RC-OPER": ("Operation outside design envelope", "Operational"),
    "RC-CONTAM": ("Contamination (dust, moisture, process deposits)", "Environmental"),
    "RC-MAINT": ("Maintenance error", "Maintenance"),
    "RC-MATL": ("Material or manufacturing defect", "Design/Supply"),
    "RC-DESIGN": ("Design deficiency", "Design/Supply"),
}
RC_WEIGHTS = {
    "BRG-WEAR": {"RC-WEAR": .45, "RC-LUB": .30, "RC-CONTAM": .15, "RC-INST": .10},
    "SEAL-LEAK": {"RC-WEAR": .40, "RC-OPER": .25, "RC-INST": .20, "RC-MATL": .15},
    "CAVIT": {"RC-OPER": .60, "RC-DESIGN": .40},
    "MISALIGN": {"RC-INST": .60, "RC-MAINT": .40},
    "IMBAL": {"RC-CONTAM": .40, "RC-WEAR": .30, "RC-MAINT": .30},
    "LUB-FAIL": {"RC-LUB": .60, "RC-MAINT": .25, "RC-CONTAM": .15},
    "WIND-INS": {"RC-OPER": .40, "RC-WEAR": .35, "RC-CONTAM": .25},
    "OVERHEAT": {"RC-CONTAM": .40, "RC-OPER": .40, "RC-DESIGN": .20},
    "GEAR-WEAR": {"RC-WEAR": .50, "RC-LUB": .35, "RC-MATL": .15},
    "VALVE-FAIL": {"RC-WEAR": .50, "RC-CONTAM": .30, "RC-MATL": .20},
    "CLOG": {"RC-CONTAM": .80, "RC-OPER": .20},
}
# code: (name, family, unit cost range EUR)
COMPONENTS = {
    "BRG-DE": ("Drive-end bearing", "Bearing", (80, 900)),
    "BRG-NDE": ("Non-drive-end bearing", "Bearing", (80, 800)),
    "SEAL-MECH": ("Mechanical seal", "Seal", (300, 2500)),
    "IMPELLER": ("Impeller", "Hydraulic", (800, 6000)),
    "COUPLING": ("Shaft coupling", "Drivetrain", (150, 800)),
    "ROTOR": ("Rotor assembly", "Rotating", (0, 0)),
    "FAN-BLADE": ("Fan blade set", "Rotating", (400, 3000)),
    "LUBE-SYS": ("Lubrication system", "Lubrication", (20, 150)),
    "STATOR-WDG": ("Stator winding", "Electrical", (1500, 8000)),
    "COOLING-SYS": ("Cooling system / heat exchanger", "Thermal", (20, 200)),
    "GEAR-SET": ("Gear set", "Drivetrain", (2000, 15000)),
    "VALVE": ("Valve assembly", "Hydraulic", (200, 1500)),
    "STRAINER": ("Suction strainer / filter", "Filtration", (30, 250)),
    "OIL-FILTER": ("Oil filter", "Filtration", (25, 90)),
    "SENSOR-PKG": ("Sensor package", "Instrumentation", (0, 0)),
    "GENERAL": ("Whole asset", "General", (0, 0)),
}
FM_PRIMARY_ACTION = {
    "BRG-WEAR": ("Replace", "BRG-DE"), "SEAL-LEAK": ("Replace", "SEAL-MECH"), "CAVIT": ("Repair", "IMPELLER"),
    "MISALIGN": ("Align", "COUPLING"), "IMBAL": ("Adjust", "ROTOR"), "LUB-FAIL": ("Service", "LUBE-SYS"),
    "WIND-INS": ("Repair", "STATOR-WDG"), "OVERHEAT": ("Clean", "COOLING-SYS"),
    "GEAR-WEAR": ("Replace", "GEAR-SET"), "VALVE-FAIL": ("Replace", "VALVE"), "CLOG": ("Clean", "STRAINER"),
}
PM_TASKS = [("Lubricate", "BRG-DE"), ("Service", "LUBE-SYS"), ("Replace", "OIL-FILTER"),
            ("Inspect", "GENERAL"), ("Calibrate", "SENSOR-PKG"), ("Clean", "COOLING-SYS"), ("Align", "COUPLING")]
ACTION_TEXT = {"Replace": "Replaced", "Repair": "Repaired", "Service": "Serviced", "Adjust": "Balanced/adjusted",
               "Clean": "Cleaned", "Calibrate": "Calibrated", "Inspect": "Inspected", "Lubricate": "Lubricated",
               "Align": "Laser-aligned"}
TECHNICIANS = {"PLT-A": [f"TECH-0{i}" for i in range(1, 5)], "PLT-B": [f"TECH-0{i}" for i in range(5, 9)]}

# Fictional monthly FX rates: 1 unit of currency = rate_to_eur EUR.
_MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
FX_RATES = {("EUR", m): 1.0 for m in _MONTHS}
FX_RATES.update({("USD", m): r for m, r in zip(_MONTHS, [0.918, 0.921, 0.915, 0.909, 0.912, 0.906, 0.903])})
FX_RATES.update({("GBP", m): r for m, r in zip(_MONTHS, [1.168, 1.172, 1.165, 1.170, 1.176, 1.173, 1.169])})

# =============================================================================
# 3. Column contracts (exact order from data_dictionary.md)
# =============================================================================
AUDIT_COLUMNS = ["source_file", "ingestion_ts", "batch_id", "source_system", "source_system_key"]
EQUIPMENT_COLUMNS = [
    "equipment_id", "equipment_name", "asset_tag", "equipment_type", "manufacturer", "model_number",
    "serial_number", "site_code", "site_name", "country", "site_timezone", "production_line",
    "parent_equipment_id", "install_date", "decommission_date", "criticality", "rated_power_kw",
    "rated_speed_rpm", "rated_temp_max_c", "rated_vibration_max_mm_s", "rated_pressure_max_bar",
    "operating_hours_per_day", "operating_days_per_week", "status", "effective_from",
    "last_modified_ts", "record_version"] + AUDIT_COLUMNS
WORK_ORDER_COLUMNS = [
    "work_order_id", "equipment_id", "wo_type", "priority", "failure_mode_code", "failure_mode_desc",
    "description", "requested_by_role", "assigned_technician_id", "created_ts", "due_ts",
    "actual_start_ts", "downtime_start_ts", "downtime_end_ts", "downtime_minutes", "downtime_type",
    "closed_ts", "status", "cancellation_reason", "root_cause_code", "triggering_event_id",
    "labor_hours", "labor_cost", "parts_cost", "currency", "last_modified_ts", "record_version"] + AUDIT_COLUMNS
MAINTENANCE_COLUMNS = [
    "maintenance_id", "work_order_id", "equipment_id", "activity_start_ts", "activity_end_ts",
    "activity_type", "component", "component_code", "failure_mode_code", "root_cause_code",
    "detection_method", "action_description", "parts_qty", "parts_cost_eur", "labor_hours",
    "technician_id", "outcome", "next_due_date", "last_modified_ts", "record_version"] + AUDIT_COLUMNS


# =============================================================================
# 4. Small helpers
# =============================================================================
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def hours(h: float) -> timedelta:
    return timedelta(hours=h)


def rdt(rng: random.Random, a: datetime, b: datetime) -> datetime:
    """Uniform random timestamp in [a, b), truncated to whole seconds."""
    return a + timedelta(seconds=int(rng.uniform(0, max(1.0, (b - a).total_seconds()))))


def iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=UTC)


def split_evenly(total: int, keys: Iterable) -> dict:
    keys = list(keys)
    base, rem = divmod(total, len(keys))
    return {k: base + (1 if i < rem else 0) for i, k in enumerate(keys)}


def weighted_choice(rng: random.Random, weights: dict) -> Any:
    keys = list(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def weighted_sample(rng: random.Random, items: list, weights: list[float], k: int) -> list:
    """Weighted sampling without replacement (Efraimidis-Spirakis)."""
    if k > len(items):
        raise ValueError(f"cannot sample {k} from {len(items)}")
    keyed = sorted(((rng.random() ** (1.0 / w) if w > 0 else -1.0, i) for i, w in enumerate(weights)),
                   reverse=True)
    return [items[i] for _, i in keyed[:k]]


def take(rng: random.Random, pool: list, k: int, used: set, what: str) -> list:
    """Sample k items (by id()) not yet used, mark them used."""
    free = [x for x in pool if id(x) not in used]
    if len(free) < k:
        raise RuntimeError(f"not enough candidates for {what}: need {k}, have {len(free)}")
    chosen = rng.sample(free, k)
    used.update(id(x) for x in chosen)
    return chosen


def fla_amps(power_kw: float) -> float:
    """Full-load current of a 400 V 3-phase motor (PF 0.87, efficiency 0.94)."""
    return round(power_kw * 1000 / (math.sqrt(3) * 400 * 0.87 * 0.94), 1)


def batch_of(ts: datetime) -> int:
    return 1 if ts < CUTOFF else 2


def set_audit(row: dict, batch: int, system: str, file_prefix: str, ext: str, key: str | None) -> None:
    b = BATCHES[batch]
    row["source_file"] = f"{file_prefix}_{b['stamp']}.{ext}"
    row["ingestion_ts"] = b["ingestion_ts"]
    row["batch_id"] = b["batch_id"]
    row["source_system"] = system
    row["source_system_key"] = f"{system}|{key or ''}"


# =============================================================================
# 5. Equipment master
# =============================================================================
def build_assets(rng: random.Random, n: int) -> list[dict]:
    names = list(EQUIPMENT_TYPES)
    weights = [EQUIPMENT_TYPES[t]["weight"] for t in names]
    tag_seq: Counter = Counter()
    d0, d1 = date(2006, 1, 1), date(2026, 3, 1)
    assets = []
    for i in range(n):
        etype = rng.choices(names, weights)[0]
        spec = EQUIPMENT_TYPES[etype]
        site = "PLT-A" if rng.random() < 0.55 else "PLT-B"
        mfr = rng.choice(spec["manufacturers"])
        power = rng.choice([p for p in IEC_POWER_KW if spec["power"][0] <= p <= spec["power"][1]])
        if rng.random() < 0.012:  # a few assets commissioned during the data span
            install = (SPAN_START + timedelta(days=rng.uniform(0, (SPAN_END - SPAN_START).days - 1))).date()
        else:
            install = d0 + timedelta(days=rng.randrange((d1 - d0).days))
        hpd = 24 if rng.random() < 0.6 else 16
        dpw = rng.choices([7, 6, 5], [.7, .2, .1])[0] if hpd == 24 else rng.choices([5, 6], [.6, .4])[0]
        decom = None
        if install < date(2023, 1, 1) and rng.random() < 0.05:
            decom = date(2025, 6, 1) + timedelta(days=rng.randrange((date(2026, 9, 10) - date(2025, 6, 1)).days))
        tag_seq[(spec["prefix"], site)] += 1
        tag = f"{spec['prefix']}-{site[-1]}{tag_seq[(spec['prefix'], site)]:04d}"
        assets.append({
            "idx": i, "equipment_id": f"EQ-{10001 + i}", "equipment_type": etype, "asset_tag": tag,
            "equipment_name": f"{etype} {tag}", "manufacturer": MANUFACTURERS[mfr],
            "model_number": f"{mfr}-{spec['prefix']}{int(power)}{rng.choice('ABCDEFGH')}",
            "serial_number": f"{mfr}{install.year % 100:02d}{i:06d}", "site_code": site,
            "production_line": rng.choice(LINES), "parent_equipment_id": None, "install_date": install,
            "decommission_date": decom, "criticality": rng.choices("ABC", spec["crit_w"])[0],
            "rated_power_kw": float(power), "rated_speed_rpm": rng.choice(spec["speeds"]),
            "rated_temp_max_c": float(spec["temp_alarm"]), "rated_vibration_max_mm_s": spec["vib_alarm"],
            "rated_pressure_max_bar": spec["pressure"][0] if spec["pressure"] else None,
            "operating_hours_per_day": hpd, "operating_days_per_week": dpw,
            "fla": fla_amps(power), "base_load": rng.uniform(0.6, 0.85),
        })
    return assets


def service_window(a: dict) -> tuple[datetime, datetime]:
    start = max(day_start(a["install_date"]) + hours(8), SPAN_START)
    end = SPAN_END if a["decommission_date"] is None else min(SPAN_END, day_start(a["decommission_date"]))
    return start, end


def assign_parents(rng: random.Random, assets: list[dict], excluded: set) -> None:
    """Motor -> driven machine (pump, compressor, fan); conveyor motor -> gearbox."""
    by_loc = defaultdict(list)
    for a in assets:
        if a["idx"] not in excluded:
            by_loc[(a["site_code"], a["production_line"], a["equipment_type"])].append(a)
    for a in assets:
        if a["idx"] in excluded or rng.random() > 0.4:
            continue
        loc = (a["site_code"], a["production_line"])
        if a["equipment_type"] == "Induction Motor":
            cands = [p for t in ("Centrifugal Pump", "Boiler Feed Pump", "Air Compressor", "Cooling Tower Fan")
                     for p in by_loc[loc + (t,)]]
        elif a["equipment_type"] == "Conveyor Drive Motor":
            cands = by_loc[loc + ("Gearbox",)]
        else:
            continue
        if cands:
            a["parent_equipment_id"] = rng.choice(cands)["equipment_id"]


def choose_monitored(rng: random.Random, assets: list[dict]) -> list[dict]:
    """IoT-monitored subset: in service for the whole span, one of each type first."""
    cands = [a for a in assets if a["decommission_date"] is None and a["install_date"] < date(2025, 9, 1)]
    w = {"A": 3.0, "B": 1.5, "C": 0.7}
    chosen: list[dict] = []
    for etype in EQUIPMENT_TYPES:
        pool = [a for a in cands if a["equipment_type"] == etype]
        chosen += weighted_sample(rng, pool, [w[a["criticality"]] for a in pool], 1)
    legacy = [a for a in cands if GATEWAY_FIRMWARE[(a["site_code"], a["production_line"])] == LEGACY_FIRMWARE
              and a not in chosen]
    need = max(0, N_LEGACY_FIRMWARE_MONITORED - sum(
        GATEWAY_FIRMWARE[(a["site_code"], a["production_line"])] == LEGACY_FIRMWARE for a in chosen))
    chosen += weighted_sample(rng, legacy, [w[a["criticality"]] for a in legacy], need)
    rest = [a for a in cands if a not in chosen]
    chosen += weighted_sample(rng, rest, [w[a["criticality"]] for a in rest], N_MONITORED_ASSETS - len(chosen))
    return chosen


def site_attrs(site: str) -> dict:
    s = SITES[site]
    return {"site_name": s["site_name"], "country": s["country"], "site_timezone": s["site_timezone"]}


def equipment_row(a: dict, version: int, effective_from: date, last_modified: datetime) -> dict:
    row = {k: a.get(k) for k in EQUIPMENT_COLUMNS if k in a}
    row.update(site_attrs(a["site_code"]))
    row["status"] = "Decommissioned" if a["decommission_date"] else "Active"
    row["effective_from"] = effective_from
    row["last_modified_ts"] = last_modified
    row["record_version"] = version
    row["__order"] = (a["idx"], version, 0)
    return row


def build_equipment_rows(rng: random.Random, assets: list[dict], orphans: set, monitored: set,
                         plan: dict) -> list[dict]:
    rows: list[dict] = []
    parent_refs = {a["parent_equipment_id"] for a in assets if a["parent_equipment_id"]}
    # --- version 1 rows ---------------------------------------------------
    for a in assets:
        inst = day_start(a["install_date"])
        if a["decommission_date"]:
            lm = day_start(a["decommission_date"]) + hours(rng.uniform(8, 40))
        elif inst < CUTOFF - timedelta(days=2):
            lm = rdt(rng, max(inst, datetime(2024, 1, 1, tzinfo=UTC)) + hours(6), CUTOFF - timedelta(days=1))
        else:
            lm = inst + hours(rng.uniform(4, 20))
        rows.append(equipment_row(a, 1, a["install_date"], lm))
    # --- version 2 rows (SCD type 2) ---------------------------------------
    cands = [a for a in assets if a["idx"] not in orphans and a["decommission_date"] is None
             and a["install_date"] < date(2026, 1, 1)]
    changed = set()
    for a in rng.sample(cands, N_EQUIPMENT_VERSIONS):
        a2 = dict(a)
        r = rng.random()
        transferable = (a["idx"] not in monitored and a["parent_equipment_id"] is None
                        and a["equipment_id"] not in parent_refs)
        if r < 0.15 and transferable:
            a2["site_code"] = "PLT-B" if a["site_code"] == "PLT-A" else "PLT-A"
            a2["production_line"] = rng.choice(LINES)
        elif r < 0.30 and a["idx"] not in monitored and a["parent_equipment_id"] is None \
                and a["equipment_id"] not in parent_refs:
            a2["production_line"] = rng.choice([l for l in LINES if l != a["production_line"]])
        else:
            a2["criticality"] = rng.choice([c for c in "ABC" if c != a["criticality"]])
        if rng.random() < 0.4:
            eff = (CUTOFF + timedelta(days=rng.uniform(0, 5.5))).date()
        else:
            eff = (SPAN_START + timedelta(days=rng.uniform(0, (CUTOFF - SPAN_START).days - 1))).date()
        rows.append(equipment_row(a2, 2, eff, day_start(eff) + hours(rng.uniform(6, 20))))
        changed.add(a["idx"])
    # --- audit -------------------------------------------------------------
    for row in rows:
        set_audit(row, batch_of(row["last_modified_ts"]), "EAM", "eam_equipment_extract", "csv",
                  row["equipment_id"])
        row["__defect"] = None
    # --- injected defects --------------------------------------------------
    v1 = {r["__order"][0]: r for r in rows if r["record_version"] == 1}
    used: set = set()
    simple = [v1[i] for i in sorted(v1) if i not in orphans and i not in monitored and i not in changed]

    for i in sorted(orphans):  # missing key: incomplete records never referenced elsewhere
        r = v1[i]
        r["equipment_id"] = None
        r["source_system_key"] = "EAM|"
        r["__defect"] = ("missing_equipment_key", "equipment_id is null (incomplete master record)")
        used.add(id(r))
    assert len(orphans) == plan["missing_equipment_key"]

    for r in take(rng, simple, plan["out_of_range_value"], used, "equipment out_of_range"):
        v = rng.choice(["neg_power", "power_x1000", "speed_zero", "hours_30", "neg_vib"])
        if v == "neg_power":
            r["rated_power_kw"] = -r["rated_power_kw"]
            detail = "rated_power_kw negative"
        elif v == "power_x1000":
            r["rated_power_kw"] = r["rated_power_kw"] * 1000
            detail = "rated_power_kw implausible (x1000, entered in W)"
        elif v == "speed_zero":
            r["rated_speed_rpm"] = rng.choice([0, 99999])
            detail = f"rated_speed_rpm implausible ({r['rated_speed_rpm']})"
        elif v == "hours_30":
            r["operating_hours_per_day"] = 30
            detail = "operating_hours_per_day > 24"
        else:
            r["rated_vibration_max_mm_s"] = -r["rated_vibration_max_mm_s"]
            detail = "rated_vibration_max_mm_s negative"
        r["__defect"] = ("out_of_range_value", detail)

    for r in take(rng, simple, plan["unit_inconsistency"], used, "equipment unit"):
        opts = ["temp_f", "vib_in_s"] + (["pressure_psi"] if r["rated_pressure_max_bar"] else [])
        v = rng.choice(opts)
        if v == "temp_f":
            r["rated_temp_max_c"] = round(r["rated_temp_max_c"] * 9 / 5 + 32, 1)
            detail = "rated_temp_max_c delivered in degF"
        elif v == "vib_in_s":
            r["rated_vibration_max_mm_s"] = round(r["rated_vibration_max_mm_s"] / 25.4, 3)
            detail = "rated_vibration_max_mm_s delivered in in/s"
        else:
            r["rated_pressure_max_bar"] = round(r["rated_pressure_max_bar"] * 14.5038, 1)
            detail = "rated_pressure_max_bar delivered in psi"
        r["__defect"] = ("unit_inconsistency", detail)

    decom_pool = [r for r in simple if r["decommission_date"] is not None]
    for r in take(rng, decom_pool, plan["closure_before_creation"], used, "equipment closure"):
        r["install_date"] = r["decommission_date"] + timedelta(days=rng.randint(30, 900))
        r["effective_from"] = r["install_date"]
        r["__defect"] = ("closure_before_creation", "decommission_date earlier than install_date")

    for src in take(rng, simple, plan["duplicate_record"], used, "equipment duplicates"):
        dup = dict(src)
        dup["__order"] = src["__order"][:2] + (1,)
        dup["__defect"] = ("duplicate_record", "exact duplicate row")
        rows.append(dup)

    rows.sort(key=lambda r: r["__order"])
    return rows


# =============================================================================
# 6. Work orders (internal model)
# =============================================================================
def pick_priority(rng: random.Random, wo_type: str, crit: str) -> str:
    if wo_type == "Corrective":
        w = {"A": [.6, .4, 0, 0], "B": [.3, .5, .2, 0], "C": [0, .4, .5, .1]}[crit]
    elif wo_type == "Predictive":
        w = {"A": [0, .6, .4, 0], "B": [0, .4, .5, .1], "C": [0, .2, .6, .2]}[crit]
    else:
        w = {"A": [0, 0, .6, .4], "B": [0, 0, .4, .6], "C": [0, 0, .3, .7]}[crit]
    return rng.choices(["P1", "P2", "P3", "P4"], w)[0]


def new_wo(rng: random.Random, asset: dict, wo_type: str, created: datetime, fm: str | None = None,
           monitored: bool = False) -> dict:
    prio = pick_priority(rng, wo_type, asset["criticality"])
    if wo_type in ("Corrective", "Predictive"):
        fm = fm or rng.choice(EQUIPMENT_TYPES[asset["equipment_type"]]["failure_modes"])
        rc = weighted_choice(rng, RC_WEIGHTS[fm])
    else:
        fm, rc = "NONE", None
    requested = {"Corrective": weighted_choice(rng, {"Operator": .6, "Shift Supervisor": .4}),
                 "Predictive": "Condition Monitoring", "Preventive": "Planner", "Inspection": "Planner"}[wo_type]
    detection = {"Corrective": weighted_choice(rng, {"operator_report": .6, "alarm": .2, "inspection": .2}),
                 "Predictive": "condition_monitoring", "Preventive": "scheduled_pm",
                 "Inspection": "inspection"}[wo_type]
    return {
        "asset": asset, "equipment_id": asset["equipment_id"], "wo_type": wo_type, "priority": prio,
        "fm": fm, "rc": rc, "created": created, "due": created + hours(SLA_HOURS[prio]),
        "has_dt": False, "ds": None, "de": None, "work_start": None, "work_end": None, "close": None,
        "cancel_at": None, "cancel_reason": None, "backlog": False, "temporary_fix": False,
        "monitored": monitored, "is_repeat": False, "triggering_event_id": None, "protected": False,
        "defect": None, "row_equipment_id": "__same__", "episode": None, "straddler": False,
        "technician": rng.choice(TECHNICIANS[asset["site_code"]]),
        "currency": weighted_choice(rng, {"EUR": .93, "GBP": .04, "USD": .03}),
        "requested_by": requested, "detection": detection, "overlap_with": None,
    }


def set_generic_timeline(rng: random.Random, wo: dict) -> None:
    """Planned work window, downtime and closure for a work order."""
    c, t, sla = wo["created"], wo["wo_type"], SLA_HOURS[wo["priority"]]
    if t == "Corrective":
        dur = clamp(rng.lognormvariate(math.log(5), 0.8), 1.0, 72.0)
        resp = {"P1": (0.2, 1.5), "P2": (0.5, 6), "P3": (2, 24), "P4": (4, 48)}[wo["priority"]]
        if rng.random() < 0.9:
            ds = c - timedelta(minutes=rng.uniform(0, 30))
            de = ds + hours(dur)
            ws = c + hours(min(rng.uniform(*resp), 0.3 * dur))
            we = max(de - hours(min(0.4, 0.1 * dur)), ws + hours(0.25))
            de = max(de, we + hours(0.1))
            wo.update(has_dt=True, ds=ds, de=de, work_start=ws, work_end=we)
        else:  # degraded but still running
            ws = c + hours(rng.uniform(*resp))
            wo.update(work_start=ws, work_end=ws + hours(rng.uniform(1, 6)))
        base_end = wo["de"] if wo["has_dt"] else wo["work_end"]
        wo["close"] = base_end + hours(rng.uniform(0.5, 72))
        return
    if t == "Preventive":
        lead = sla * (rng.uniform(0.1, 0.95) if rng.random() < 0.9 else rng.uniform(1.0, 1.6))
        dur, p_dt = rng.uniform(1, 6), 0.6
    elif t == "Inspection":
        lead, dur, p_dt = sla * rng.uniform(0.1, 0.9), rng.uniform(0.5, 2), 0.2
    else:  # Predictive
        lead, dur, p_dt = rng.uniform(24, 120), rng.uniform(2, 8), 0.7
    ws = c + hours(lead)
    we = ws + hours(dur)
    wo.update(work_start=ws, work_end=we)
    if rng.random() < p_dt:
        wo.update(has_dt=True, ds=ws - hours(rng.uniform(0.1, 0.5)), de=we + hours(rng.uniform(0.1, 0.5)))
    wo["close"] = (wo["de"] if wo["has_dt"] else we) + hours(rng.uniform(0.5, 72))


def shift_wo(wo: dict, delta: timedelta) -> None:
    for k in ("created", "due", "ds", "de", "work_start", "work_end", "close", "cancel_at"):
        if wo[k] is not None:
            wo[k] = wo[k] + delta


def type_weight(a: dict, wo_type: str) -> float:
    crit = a["criticality"]
    if wo_type == "Corrective":
        age = (SPAN_START.date() - a["install_date"]).days / 365.25
        duty = a["operating_hours_per_day"] * a["operating_days_per_week"] / 168
        return {"A": 3.0, "B": 1.6, "C": 1.0}[crit] * (1 + max(age, 0) / 10) * (0.5 + duty)
    if wo_type == "Predictive":
        return {"A": 2.0, "B": 1.2, "C": 0.6}[crit]
    return {"A": 1.6, "B": 1.2, "C": 1.0}[crit]


def build_generic_wos(rng: random.Random, pool: list[dict], n_total: int) -> list[dict]:
    windows = {a["idx"]: service_window(a) for a in pool}
    pool = [a for a in pool if windows[a["idx"]][1] - windows[a["idx"]][0] >= timedelta(days=10)]
    mix = {"Corrective": .30, "Preventive": .40, "Predictive": .12, "Inspection": .18}
    counts = {k: int(n_total * v) for k, v in mix.items()}
    counts["Preventive"] += n_total - sum(counts.values())
    n_repeat = round(counts["Corrective"] * 0.08)
    wos: list[dict] = []
    for wo_type, cnt in counts.items():
        k = cnt - (n_repeat if wo_type == "Corrective" else 0)
        chosen = rng.choices(pool, weights=[type_weight(a, wo_type) for a in pool], k=k)
        for a in chosen:
            s, e = windows[a["idx"]]
            wo = new_wo(rng, a, wo_type, rdt(rng, s, e - hours(1)))
            set_generic_timeline(rng, wo)
            wos.append(wo)
    # Repeat failures: same asset + failure mode within 30 days of the previous repair.
    parents = [w for w in wos if w["wo_type"] == "Corrective" and w["has_dt"]
               and w["de"] < SPAN_END - timedelta(days=5)]
    made, tries = 0, 0
    while made < n_repeat and tries < n_repeat * 50:
        tries += 1
        p = rng.choice(parents)
        created = p["de"] + timedelta(days=rng.uniform(2, 28))
        if created >= windows[p["asset"]["idx"]][1] - hours(1):
            continue
        child = new_wo(rng, p["asset"], "Corrective", created, fm=p["fm"])
        set_generic_timeline(rng, child)
        child["is_repeat"] = child["protected"] = p["protected"] = True
        p["temporary_fix"] = p["temporary_fix"] or rng.random() < 0.6
        wos.append(child)
        made += 1
    # Cancelled and never-started (backlog) work orders.
    for w in wos:
        if w["wo_type"] == "Corrective" or w["protected"]:
            continue
        r = rng.random()
        if r < 0.03:
            w.update(cancel_at=w["created"] + hours(rng.uniform(2, 96)), has_dt=False, ds=None, de=None,
                     work_start=None, work_end=None, close=None)
        elif r < 0.06 and w["created"] < SPAN_END - timedelta(days=30):
            w.update(backlog=True, has_dt=False, ds=None, de=None, work_start=None, work_end=None, close=None)
    return wos


def remove_overlaps(rng: random.Random, wos: list[dict]) -> None:
    """Ensure NO accidental downtime overlap per asset (overlaps are injected later on purpose)."""
    by_asset = defaultdict(list)
    for w in wos:
        if w["has_dt"]:
            by_asset[w["equipment_id"]].append(w)
    for eq in sorted(by_asset):
        prev_end = None
        for w in sorted(by_asset[eq], key=lambda x: x["ds"]):
            if prev_end is not None and w["ds"] < prev_end + hours(1):
                delta = prev_end + hours(rng.uniform(1, 24)) - w["ds"]
                if w["close"] + delta < service_window(w["asset"])[1] and w["created"] + delta < SPAN_END - hours(1):
                    shift_wo(w, delta)
                else:
                    w.update(has_dt=False, ds=None, de=None)
                    continue
            prev_end = w["de"]


def plan_monitored_asset(rng: random.Random, a: dict) -> tuple[list[dict], list[dict]]:
    """Degradation episodes (caught -> predictive WO, missed -> corrective WO), PMs and inspections."""
    spec = EQUIPMENT_TYPES[a["equipment_type"]]
    n_target = {"A": 3, "B": 2, "C": rng.choice([1, 2])}[a["criticality"]]
    limit = SPAN_END - timedelta(days=2)
    episodes: list[dict] = []
    cursor = SPAN_START + timedelta(days=rng.uniform(3, 35))

    def make_episode(fm: str, t0: datetime, d: timedelta, caught: bool, repeat: bool) -> dict:
        tf = t0 + d
        tc = None
        if caught:
            tc = t0 + d * rng.uniform(0.65, 0.85)
            wo = new_wo(rng, a, "Predictive", tc, fm, monitored=True)
            ds = min(tc + hours(rng.uniform(4, 48)), t0 + d * 0.97)
            de = ds + hours(rng.uniform(3, 10))
            wo.update(has_dt=True, ds=ds, de=de, work_start=ds + hours(rng.uniform(0.1, 0.3)),
                      work_end=de - hours(rng.uniform(0.1, 0.3)))
        else:
            wo = new_wo(rng, a, "Corrective", tf + timedelta(minutes=rng.uniform(5, 30)), fm, monitored=True)
            dur = clamp(rng.lognormvariate(math.log(8), 0.6), 2, 48)
            ds, de = tf, tf + hours(dur)
            wo.update(has_dt=True, ds=ds, de=de,
                      work_start=wo["created"] + hours(min(rng.uniform(0.3, 2.0), 0.3 * dur)),
                      work_end=de - hours(min(0.4, 0.1 * dur)))
        wo["close"] = de + hours(rng.uniform(1, 48))
        wo["is_repeat"] = repeat
        wo["protected"] = True
        ep = {"t0": t0, "D": d, "fm": fm, "ds": ds, "de": de, "tc": tc, "caught": caught, "wo": wo}
        wo["episode"] = ep
        return ep

    made = 0
    while made < n_target:
        fm = rng.choice(spec["failure_modes"])
        d = timedelta(days=rng.uniform(7, 45))
        if cursor + d >= limit:
            break
        ep = make_episode(fm, cursor, d, rng.random() < 0.45, repeat=False)
        episodes.append(ep)
        made += 1
        cursor = ep["de"] + timedelta(days=rng.uniform(10, 45))
        if not ep["caught"] and rng.random() < 0.35:  # temporary fix -> repeat failure < 30 days
            ep["wo"]["temporary_fix"] = True
            t0r = ep["de"] + timedelta(days=rng.uniform(1, 4))
            dr = timedelta(days=rng.uniform(7, 20))
            if t0r + dr < limit:
                rep = make_episode(fm, t0r, dr, caught=False, repeat=True)
                episodes.append(rep)
                cursor = rep["de"] + timedelta(days=rng.uniform(10, 45))
    wos = [e["wo"] for e in episodes]
    busy = [(e["t0"] - timedelta(days=1), e["de"] + timedelta(days=1)) for e in episodes]

    def free_slot(t: datetime) -> datetime:
        for _ in range(20):
            hit = [b for b in busy if b[0] <= t <= b[1]]
            if not hit:
                return t
            t = max(b[1] for b in hit) + timedelta(days=rng.uniform(1, 3))
        return t

    t = SPAN_START + timedelta(days=rng.uniform(10, 40))
    while True:  # preventive maintenance outside degradation episodes
        t = free_slot(t)
        if t >= SPAN_END - timedelta(days=3):
            break
        created = max(SPAN_START + hours(1), t - timedelta(days=rng.uniform(3, 10)))
        wo = new_wo(rng, a, "Preventive", created, monitored=True)
        de = t + hours(rng.uniform(2, 5))
        wo.update(has_dt=True, ds=t, de=de, work_start=t + hours(0.1), work_end=de - hours(0.1),
                  close=de + hours(rng.uniform(1, 48)), protected=True)
        wos.append(wo)
        busy.append((t - timedelta(days=1), de + timedelta(days=1)))
        t = de + timedelta(days=rng.uniform(40, 70))
    for _ in range(rng.randint(1, 3)):  # inspections without downtime
        t = free_slot(rdt(rng, SPAN_START + timedelta(days=5), SPAN_END - timedelta(days=3)))
        if t >= SPAN_END - timedelta(days=2):
            continue
        created = max(SPAN_START + hours(1), t - timedelta(days=rng.uniform(1, 5)))
        wo = new_wo(rng, a, "Inspection", created, monitored=True)
        we = t + hours(rng.uniform(0.5, 1.5))
        wo.update(work_start=t, work_end=we, close=we + hours(rng.uniform(1, 24)), protected=True)
        wos.append(wo)
    return episodes, wos


def inject_overlapping_downtime(rng: random.Random, wos: list[dict], n: int) -> None:
    """Move work order B onto asset A's asset so its downtime starts inside A's downtime."""
    limit = CUTOFF - timedelta(days=5)
    intervals = defaultdict(list)
    for w in wos:
        if w["has_dt"]:
            intervals[w["equipment_id"]].append(w)
    cand = [w for w in wos if not w["monitored"] and w["has_dt"] and not w["protected"]
            and w["cancel_at"] is None and not w["backlog"] and w["close"] < limit
            and w["de"] - w["ds"] >= hours(2)]
    rng.shuffle(cand)
    used: set = set()
    made = 0
    for a_wo in cand:
        if made == n:
            break
        if id(a_wo) in used:
            continue
        for b_wo in cand:
            if id(b_wo) in used or b_wo is a_wo or b_wo["equipment_id"] == a_wo["equipment_id"]:
                continue
            new_ds = a_wo["ds"] + (a_wo["de"] - a_wo["ds"]) * rng.uniform(0.2, 0.8)
            delta = new_ds - b_wo["ds"]
            new_de = b_wo["de"] + delta
            s, e = service_window(a_wo["asset"])
            if (b_wo["created"] + delta < max(s, SPAN_START) or b_wo["close"] + delta >= CUTOFF - timedelta(days=2)
                    or b_wo["close"] + delta >= e):
                continue
            clash = [o for o in intervals[a_wo["equipment_id"]]
                     if o is not a_wo and o["ds"] < new_de + hours(1) and new_ds < o["de"] + hours(1)]
            if clash:
                continue
            intervals[b_wo["equipment_id"]].remove(b_wo)
            shift_wo(b_wo, delta)
            asset = a_wo["asset"]
            b_wo["asset"], b_wo["equipment_id"] = asset, asset["equipment_id"]
            b_wo["technician"] = rng.choice(TECHNICIANS[asset["site_code"]])
            fms = EQUIPMENT_TYPES[asset["equipment_type"]]["failure_modes"]
            if b_wo["fm"] != "NONE" and b_wo["fm"] not in fms:
                b_wo["fm"] = rng.choice(fms)
                b_wo["rc"] = weighted_choice(rng, RC_WEIGHTS[b_wo["fm"]])
            intervals[asset["equipment_id"]].append(b_wo)
            b_wo["defect"] = ("overlapping_downtime", None)
            b_wo["overlap_with"] = a_wo
            a_wo["protected"] = b_wo["protected"] = True
            used.update({id(a_wo), id(b_wo)})
            made += 1
            break
    if made < n:
        raise RuntimeError(f"could only inject {made} overlapping downtimes")


def wo_state(wo: dict, t: datetime) -> dict | None:
    """Source-system view of a work order as extracted at time t (exclusive)."""
    if wo["created"] >= t:
        return None
    if wo["cancel_at"] is not None:
        status = "Cancelled" if wo["cancel_at"] < t else "Open"
    elif wo["backlog"]:
        status = "Open"
    elif wo["close"] < t:
        status = "Closed"
    elif wo["work_start"] < t or (wo["has_dt"] and wo["wo_type"] == "Corrective" and wo["ds"] < t):
        status = "In Progress"
    else:
        status = "Open"
    ds = wo["ds"] if wo["has_dt"] and wo["ds"] < t else None
    de = wo["de"] if wo["has_dt"] and wo["de"] < t else None
    if status == "Closed":
        actual = wo["work_start"]  # = start of the first maintenance activity
    elif status == "In Progress":
        actual = ds if ds is not None else wo["work_start"]
    else:
        actual = None
    closed = wo["close"] if status == "Closed" else (wo["cancel_at"] if status == "Cancelled" else None)
    last_mod = max(x for x in (wo["created"], actual, ds, de, closed) if x is not None)
    return {"status": status, "actual": actual, "ds": ds, "de": de, "closed": closed, "last_mod": last_mod}


def describe(wo: dict) -> str:
    tag, fm = wo["asset"]["asset_tag"], wo["fm"]
    if wo["wo_type"] == "Corrective":
        return f"{FAILURE_MODES[fm][0]}: {FM_SYMPTOM[fm]} on {tag}"
    if wo["wo_type"] == "Predictive":
        return f"Condition monitoring: early signs of {FAILURE_MODES[fm][0].lower()} on {tag}; plan repair"
    if wo["wo_type"] == "Preventive":
        return f"{PM_INTERVAL_DAYS[wo['asset']['criticality']]}-day preventive maintenance on {tag}"
    return f"Routine condition inspection of {tag}"


# =============================================================================
# 7. Maintenance activities
# =============================================================================
def activity_plan(rng: random.Random, wo: dict, k: int) -> list[tuple[str, str]]:
    etype = wo["asset"]["equipment_type"]
    if wo["wo_type"] in ("Corrective", "Predictive"):
        act, comp = FM_PRIMARY_ACTION[wo["fm"]]
        if wo["fm"] == "IMBAL" and etype == "Cooling Tower Fan":
            act, comp = ("Replace", "FAN-BLADE") if rng.random() < 0.4 else ("Adjust", "FAN-BLADE")
        if comp == "BRG-DE" and rng.random() < 0.3:
            comp = "BRG-NDE"
        primary = (act, comp)
        if k == 1:
            return [primary]
        post = ("Align", "COUPLING") if act != "Align" else ("Inspect", "GENERAL")
        return [("Inspect", "GENERAL"), primary, post][:k] if k == 3 else [("Inspect", "GENERAL"), primary]
    if wo["wo_type"] == "Preventive":
        return rng.sample(PM_TASKS, k)
    return [("Inspect", "GENERAL")] * k


def parts_for(rng: random.Random, act: str, comp: str, power_kw: float) -> tuple[int, float]:
    scale = clamp((power_kw / 50) ** 0.3, 0.7, 3.0)
    lo, hi = COMPONENTS[comp][2]
    if act in ("Replace", "Repair") and hi > 0:
        qty = rng.randint(1, 2) if comp.startswith("BRG") or comp == "OIL-FILTER" else 1
        return qty, round(qty * rng.uniform(lo, hi) * scale, 2)
    if act == "Lubricate":
        qty = rng.randint(1, 3)
        return qty, round(qty * rng.uniform(8, 45), 2)
    if act in ("Service", "Clean") and hi > 0:
        qty = rng.randint(1, 3)
        return qty, round(qty * rng.uniform(lo, hi) * 0.5, 2)
    return 0, 0.0


def build_activities(rng: random.Random, wo: dict, k: int) -> list[dict]:
    ws, we = wo["work_start"], wo["work_end"]
    total = (we - ws).total_seconds()
    gap = min(total * 0.1 / (k - 1), 1800) if k > 1 else 0
    props = [rng.uniform(0.5, 1.5) for _ in range(k)]
    usable = total - gap * (k - 1)
    segs, cur = [], ws
    for p in props:
        end = cur + timedelta(seconds=usable * p / sum(props))
        segs.append([cur, end])
        cur = end + timedelta(seconds=gap)
    segs[-1][1] = we
    plan = activity_plan(rng, wo, k)
    a = wo["asset"]
    acts = []
    for i, ((st, en), (act, comp)) in enumerate(zip(segs, plan)):
        st, en = st.replace(microsecond=0), en.replace(microsecond=0)
        dur_h = max((en - st).total_seconds() / 3600, 0.25)
        crew = 2 if act in ("Replace", "Repair") and rng.random() < 0.5 else 1
        qty, cost = parts_for(rng, act, comp, a["rated_power_kw"])
        last = i == k - 1
        outcome = "Resolved"
        if last and wo["wo_type"] == "Corrective" and (wo["temporary_fix"] or rng.random() < 0.03):
            outcome = "Temporary fix"
        comp_text = COMPONENTS[comp][0]
        if comp == "BRG-DE" and act == "Replace" and rng.random() < 0.15:
            comp_text = "Bearing and coupling"
        next_due = None
        if wo["wo_type"] == "Preventive":
            next_due = en.date() + timedelta(days=PM_INTERVAL_DAYS[a["criticality"]])
        elif wo["wo_type"] == "Inspection":
            next_due = en.date() + timedelta(days=90)
        acts.append({
            "wo": wo, "start": st, "end": en, "activity_type": act, "component": comp_text,
            "component_code": comp, "failure_mode_code": wo["fm"], "root_cause_code": wo["rc"],
            "detection_method": wo["detection"],
            "action_description": f"{ACTION_TEXT[act]} {comp_text.lower()}"
                                  + ("; condition back within limits" if last and outcome == "Resolved" else ""),
            "parts_qty": qty, "parts_cost_eur": cost, "labor_hours": round(dur_h * crew, 2),
            "technician_id": wo["technician"] if rng.random() < 0.8 else rng.choice(TECHNICIANS[a["site_code"]]),
            "outcome": outcome, "next_due_date": next_due, "equipment_id": a["equipment_id"],
            "system": "CMMS",
        })
    return acts


def build_legacy_activities(rng: random.Random, pool: list[dict], n: int) -> list[dict]:
    acts = []
    cands = [a for a in pool if a["install_date"] < date(2025, 6, 1) and
             (a["decommission_date"] is None or a["decommission_date"] > date(2026, 3, 1))]
    for a in rng.sample(cands, n):
        fm = rng.choice(EQUIPMENT_TYPES[a["equipment_type"]]["failure_modes"])
        act, comp = FM_PRIMARY_ACTION[fm]
        st = rdt(rng, datetime(2025, 10, 1, tzinfo=UTC), datetime(2026, 3, 10, tzinfo=UTC))
        en = st + hours(rng.uniform(1, 8))
        qty, cost = parts_for(rng, act, comp, a["rated_power_kw"])
        acts.append({
            "wo": None, "start": st, "end": en, "activity_type": act, "component": COMPONENTS[comp][0],
            "component_code": comp, "failure_mode_code": fm, "root_cause_code": weighted_choice(rng, RC_WEIGHTS[fm]),
            "detection_method": rng.choice(["operator_report", "inspection"]),
            "action_description": f"{ACTION_TEXT[act]} {COMPONENTS[comp][0].lower()} (legacy record)",
            "parts_qty": qty, "parts_cost_eur": cost,
            "labor_hours": round((en - st).total_seconds() / 3600, 2),
            "technician_id": rng.choice(TECHNICIANS[a["site_code"]]), "outcome": "Resolved",
            "next_due_date": None, "equipment_id": a["equipment_id"], "system": "LEGACY_CMMS",
        })
    return acts


# =============================================================================
# 8. Sensor events
# =============================================================================
def asset_limits(a: dict) -> dict:
    spec = EQUIPMENT_TYPES[a["equipment_type"]]
    lim = {}
    for m in spec["measures"]:
        fam = MEASURES[m]["family"]
        if fam == "vibration":
            warn, alarm, rmin, rmax = VIB_WARNING[spec["vib_alarm"]], spec["vib_alarm"], 0.0, 50.0
        elif fam == "temperature":
            warn, alarm, rmin, rmax = spec["temp_alarm"] - 10.0, float(spec["temp_alarm"]), -40.0, 250.0
        elif fam == "pressure":
            alarm = spec["pressure"][0]
            warn, rmin, rmax = round(0.92 * alarm, 1), 0.0, round(2 * alarm, 1)
        elif fam == "current":
            warn, alarm, rmin, rmax = round(1.05 * a["fla"], 1), round(1.15 * a["fla"], 1), 0.0, round(3 * a["fla"], 1)
        elif fam == "speed":
            warn, alarm, rmin, rmax = None, None, 0.0, float(round(1.25 * a["rated_speed_rpm"]))
        else:
            warn, alarm, rmin, rmax = None, None, 0.0, 150.0
        lim[m] = {"warning": warn, "alarm": alarm, "range_min": rmin, "range_max": rmax}
    return lim


def is_scheduled(a: dict, t: datetime) -> bool:
    if t.weekday() >= a["operating_days_per_week"]:
        return False
    return a["operating_hours_per_day"] == 24 or 6 <= t.hour < 22


def operating_context(a: dict, windows: list[dict], t: datetime) -> tuple[str, float]:
    """(operating_state, hours since the asset stopped)."""
    for w in windows:
        if w["ds"] <= t < w["de"]:
            state = "stopped" if w["wo_type"] == "Corrective" else "maintenance"
            return state, (t - w["ds"]).total_seconds() / 3600
    if is_scheduled(a, t):
        return "running", 0.0
    for h in range(1, 97):
        if is_scheduled(a, t - hours(h)):
            return "stopped", h - 0.5
    return "stopped", 96.0


def degradation(episodes: list[dict], t: datetime) -> dict[str, float]:
    for ep in episodes:
        if ep["t0"] <= t < ep["ds"]:
            s = min(1.0, (t - ep["t0"]) / ep["D"]) ** 1.8
            return {fam: w * (s if shape == "lead" else max(0.0, (s - 0.4) / 0.6))
                    for fam, (shape, w) in FM_EFFECTS[ep["fm"]].items()}
    return {}


def ambient_c(a: dict, t: datetime) -> float:
    doy, h = t.timetuple().tm_yday, t.hour + t.minute / 60
    amb = 13 + 9 * math.sin(2 * math.pi * (doy - 110) / 365) + 4 * math.sin(2 * math.pi * (h - 9) / 24)
    amb += SITES[a["site_code"]]["ambient_offset_c"]
    return amb + (5.0 if EQUIPMENT_TYPES[a["equipment_type"]]["indoor"] else 0.0)


def sensor_value(rng: random.Random, a: dict, measure: str, t: datetime, state: str, h_stop: float,
                 eff: dict[str, float]) -> float:
    """Physically plausible reading in the canonical SI unit."""
    spec = EQUIPMENT_TYPES[a["equipment_type"]]
    fam = MEASURES[measure]["family"]
    running = state == "running"
    amb = ambient_c(a, t)
    load = 0.0
    if running:
        h = t.hour + t.minute / 60
        load = clamp(a["base_load"] + 0.08 * math.sin(2 * math.pi * (h - 10) / 24) + rng.gauss(0, 0.04), 0.3, 1.0)
    if fam == "vibration":
        if not running:
            return rng.uniform(0.05, 0.3)
        v = spec["vib_base"] * (0.75 + 0.33 * load) * rng.lognormvariate(0, 0.08)
        return v + eff.get("vib", 0) * (spec["vib_alarm"] * 1.15 - spec["vib_base"])
    if fam == "temperature":
        rise = spec["temp_base"] - 20
        if not running:  # exponential decay towards ambient, tau = 1.5 h
            return amb + rise * math.exp(-h_stop / 1.5) + rng.gauss(0, 0.4)
        temp = amb + rise * (0.7 + 0.4 * load) + rng.gauss(0, 0.8)
        return temp + eff.get("temp", 0) * max(0.0, spec["temp_alarm"] + 4 - (amb + rise))
    if fam == "pressure":
        alarm, base = spec["pressure"]
        if not running:
            return base * rng.uniform(0.0, 0.04)
        p = base * (0.88 + 0.16 * load) * (1 + rng.gauss(0, 0.012)) * (1 - 0.4 * eff.get("pres_drop", 0))
        return p + eff.get("pres_up", 0) * (alarm * 1.03 - base)
    if fam == "current":
        if not running:
            return 0.0
        return a["fla"] * (0.35 + 0.6 * load) * (1 + 0.5 * eff.get("current", 0)) * (1 + rng.gauss(0, 0.015))
    if fam == "speed":
        if not running:
            return 0.0
        rated = a["rated_speed_rpm"]
        if a["equipment_type"] == "CNC Spindle":
            return rated * rng.uniform(0.4, 1.0)
        return rated * (1 - 0.015 * load) + rng.gauss(0, rated * 0.002)
    return 0.0 if not running else load * 100 + rng.gauss(0, 1.0)


def round_measure(measure: str, v: float) -> float | int:
    dp = MEASURES[measure]["dp"]
    return int(round(v)) if dp == 0 else round(v, dp)


def gateway_alarm(lim: dict, measure: str, value: float) -> dict:
    """Alarm as computed by the gateway on the RAW value (wrong for unit errors / spikes)."""
    code = MEASURES[measure]["alarm_code"]
    if code and value >= lim["alarm"]:
        return {"state": "alarm", "code": code, "limit_value": lim["alarm"]}
    if code and value >= lim["warning"]:
        return {"state": "warning", "code": code, "limit_value": lim["warning"]}
    return {"state": "normal", "code": None, "limit_value": None}


def sensor_id_for(equipment_id: str, measure: str) -> str:
    return f"{equipment_id}-{MEASURES[measure]['code']}01"


def build_clean_events(rng: random.Random, monitored: list[dict], episodes: dict, windows: dict,
                       n_clean: int) -> list[dict]:
    sensors = [(a, m) for a in monitored for m in EQUIPMENT_TYPES[a["equipment_type"]]["measures"]]
    counts = split_evenly(n_clean, range(len(sensors)))
    span_s = (SPAN_END - SPAN_START).total_seconds()
    events = []
    for idx, (a, m) in enumerate(sensors):
        n, lim, meta = counts[idx], a["limits"][m], MEASURES[m]
        point = MEASUREMENT_POINT[m]
        sensor = {"sensor_id": sensor_id_for(a["equipment_id"], m), "sensor_type": meta["family"],
                  "measure": m, "canonical_unit": meta["unit"], "range_min": lim["range_min"],
                  "range_max": lim["range_max"], "measurement_point": point}
        step = span_s / n
        for i in range(n):
            t = SPAN_START + timedelta(seconds=int(step * (i + rng.uniform(0.1, 0.9))))
            state, h_stop = operating_context(a, windows[a["equipment_id"]], t)
            eff = degradation(episodes[a["equipment_id"]], t) if state == "running" else {}
            value = round_measure(m, sensor_value(rng, a, m, t, state, h_stop, eff))
            gw_loc = (a["site_code"], a["production_line"])
            events.append({
                "event_id": None, "record_version": 1, "event_ts": iso(t), "equipment_id": a["equipment_id"],
                "operating_state": state, "sensor": dict(sensor),
                "reading": {"value": value, "unit": meta["unit"]},
                "quality_flag": "SUSPECT" if rng.random() < 0.004 else "GOOD",
                "alarm": gateway_alarm(lim, m, value),
                "gateway": {"gateway_id": f"GW-{a['site_code'].replace('-', '')}-{a['production_line']}",
                            "firmware_version": GATEWAY_FIRMWARE[gw_loc], "received_ts": None},
                "location": {"site_code": a["site_code"], "production_line": a["production_line"]},
                "tags": [POINT_TAG[point]],
                "__t": t, "__recv": t + timedelta(seconds=rng.randint(1, 90)), "__asset": a, "__measure": m,
                "__lim": lim, "__defect": None, "__protected": False, "__late": False,
            })
    events.sort(key=lambda e: (e["__t"], e["sensor"]["sensor_id"]))
    for i, e in enumerate(events, start=1):
        e["event_id"] = f"SE-{i:06d}"
    return events


def primary_measure(a: dict, fm: str) -> str:
    fam = next(iter(FM_EFFECTS[fm]))
    spec = EQUIPMENT_TYPES[a["equipment_type"]]
    m = {"vib": "vibration_rms", "temp": spec["temp_measure"], "current": "motor_current",
         "pres_drop": "discharge_pressure", "pres_up": "discharge_pressure"}[fam]
    return m if m in spec["measures"] else "vibration_rms"


def link_sensor_evidence(rng: random.Random, monitored_wos: list[dict], events: list[dict]) -> None:
    """Predictive WOs get the alarm that triggered them; corrective WOs learn whether alarms fired."""
    by_sensor, by_asset = defaultdict(list), defaultdict(list)
    for e in events:
        by_sensor[e["sensor"]["sensor_id"]].append(e)
        by_asset[e["equipment_id"]].append(e)
    for wo in monitored_wos:
        ep = wo["episode"]
        if ep is None:
            continue
        a = wo["asset"]
        if wo["wo_type"] == "Predictive":
            sid = sensor_id_for(a["equipment_id"], primary_measure(a, ep["fm"]))
            window = [e for e in by_sensor[sid] if ep["t0"] <= e["__t"] <= ep["tc"]
                      and e["operating_state"] == "running" and e["quality_flag"] == "GOOD"]
            if not window:
                continue
            flagged = [e for e in window if e["alarm"]["state"] != "normal"]
            trig = flagged[0] if flagged else window[-1]
            trig["__protected"] = True
            wo["triggering_event_id"] = trig["event_id"]
            wo["created"] = min(ep["tc"], trig["__t"] + hours(rng.uniform(1, 8)))
            wo["due"] = wo["created"] + hours(SLA_HOURS[wo["priority"]])
        else:
            alarms = [e for e in by_asset[a["equipment_id"]] if ep["t0"] <= e["__t"] < ep["ds"]
                      and e["alarm"]["state"] == "alarm"]
            wo["detection"] = "alarm" if alarms and rng.random() < 0.7 else "operator_report"
            if wo["detection"] == "alarm":
                wo["requested_by"] = "Condition Monitoring"


def strip_internal(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if not k.startswith("__")}


def finalize_sensor_events(rng: random.Random, events: list[dict], plan: dict) -> list[dict]:
    used: set = set()
    # Late-arriving events (not defects): buffered gateway uploads delivered with batch 2.
    late_pool = [e for e in events if CUTOFF - timedelta(days=12) <= e["__t"] < CUTOFF - hours(1)
                 and not e["__protected"]]
    for e in take(rng, late_pool, N_LATE_EVENTS, used, "late events"):
        e["__recv"] = CUTOFF + hours(rng.uniform(1, 120))
        e["__late"] = True
        e["tags"] = e["tags"] + ["buffered-upload"]
    clean = [e for e in events if not e["__protected"]]
    running = [e for e in clean if e["operating_state"] == "running" and e["quality_flag"] == "GOOD"]

    # --- out-of-range readings --------------------------------------------
    corrections: list[dict] = []
    oor = take(rng, running, plan["out_of_range_reading"], used, "out-of-range")
    for e in oor:
        fam, m = e["sensor"]["sensor_type"], e["__measure"]
        e["__true_value"] = e["reading"]["value"]
        if fam in ("vibration", "current", "pressure") and rng.random() < 0.4:
            val, detail = -round(rng.uniform(0.5, 3.0), 2), "negative reading"
        elif rng.random() < 0.5 and e["sensor"]["range_max"] < 9999:
            val, detail = 9999.0, "spike 9999 (sensor fault code passed as value)"
        else:
            val = round(e["sensor"]["range_max"] * rng.uniform(1.5, 3.0), 2)
            detail = "reading above physical range_max"
        e["reading"]["value"] = val
        e["alarm"] = gateway_alarm(e["__lim"], m, val)
        e["quality_flag"] = "SUSPECT" if rng.random() < 0.7 else "GOOD"
        e["__defect"] = ("out_of_range_reading", detail)
    for e in rng.sample([x for x in oor if x["__recv"] < CUTOFF], N_SENSOR_CORRECTIONS):
        c = copy.deepcopy(strip_internal(e))
        c.update({"record_version": 2, "quality_flag": "GOOD", "tags": e["tags"] + ["reprocessed"]})
        c["reading"]["value"] = e["__true_value"]
        c["alarm"] = gateway_alarm(e["__lim"], e["__measure"], e["__true_value"])
        c.update({"__t": e["__t"], "__recv": CUTOFF + hours(rng.uniform(6, 150)), "__defect": None,
                  "__correction": {"is_correction": True, "corrects_version": 1,
                                   "reason": "Out-of-range value re-processed by IoT hub"}})
        corrections.append(c)

    # --- unit inconsistency (legacy firmware sends imperial units) ---------
    legacy = [e for e in running if e["gateway"]["firmware_version"] == LEGACY_FIRMWARE
              and e["sensor"]["sensor_type"] in ("temperature", "pressure", "vibration")]
    for e in take(rng, legacy, plan["unit_inconsistency"], used, "unit inconsistency"):
        v, fam = e["reading"]["value"], e["sensor"]["sensor_type"]
        if fam == "temperature":
            e["reading"] = {"value": round(v * 9 / 5 + 32, 1), "unit": "degF"}
        elif fam == "pressure":
            e["reading"] = {"value": round(v * 14.5038, 1), "unit": "psi"}
        else:
            e["reading"] = {"value": round(v / 25.4, 3), "unit": "in/s"}
        e["alarm"] = gateway_alarm(e["__lim"], e["__measure"], e["reading"]["value"])
        e["__defect"] = ("unit_inconsistency", f"reported in {e['reading']['unit']} instead of "
                                               f"{e['sensor']['canonical_unit']}")

    # --- missing / unknown equipment keys ----------------------------------
    miss = take(rng, clean, plan["missing_equipment_key"], used, "sensor missing key")
    for i, e in enumerate(miss):
        if i % 2 == 0:
            e["equipment_id"] = None
            e["__defect"] = ("missing_equipment_key", "equipment_id null (resolvable from sensor_id prefix)")
        else:
            e["equipment_id"] = UNKNOWN_EQUIPMENT_ID
            e["sensor"]["sensor_id"] = sensor_id_for(UNKNOWN_EQUIPMENT_ID, e["__measure"])
            e["tags"] = e["tags"] + ["unregistered"]
            e["__defect"] = ("missing_equipment_key", "unknown equipment_id EQ-9999 (unregistered device)")

    # --- near duplicates (gateway re-send with a new event_id) -------------
    next_num = len(events) + 1
    near = []
    for e in take(rng, clean, plan["duplicate_sensor_event_near"], used, "near duplicates"):
        d = copy.deepcopy(strip_internal(e))
        d["event_id"] = f"SE-{next_num:06d}"
        next_num += 1
        if rng.random() < 0.5 and e["operating_state"] == "running" and e["reading"]["value"] > 1:
            dp = MEASURES[e["__measure"]]["dp"]
            step = 1 if dp == 0 else 10 ** -dp
            d["reading"]["value"] = round_measure(e["__measure"], e["reading"]["value"] + rng.choice([-1, 1]) * step)
            d["alarm"] = gateway_alarm(e["__lim"], e["__measure"], d["reading"]["value"])
        d.update({"__t": e["__t"], "__recv": e["__recv"] + timedelta(seconds=rng.randint(1, 30)),
                  "__defect": ("duplicate_sensor_event_near",
                               f"same equipment/sensor/event_ts as {e['event_id']}, new event_id")})
        near.append(d)
    exact_src = take(rng, clean, plan["duplicate_sensor_event_exact"], used, "exact duplicates")

    # --- finalize: received_ts, batch metadata, schema drift ---------------
    final = [e for e in events] + corrections + near
    for e in final:
        b = batch_of(e["__recv"])
        e["gateway"]["received_ts"] = iso(e["__recv"])
        meta = BATCHES[b]
        e["_metadata"] = {"source_file": f"iot_hub_sensor_events_{meta['stamp']}.json",
                          "ingestion_ts": iso(meta["ingestion_ts"]), "batch_id": meta["batch_id"],
                          "source_system": "IOT_HUB", "source_system_key": f"IOT_HUB|{e['event_id']}"}
        if b == 2:  # schema drift: the correction struct only exists in batch 2
            e["correction"] = e.get("__correction") or {"is_correction": False, "corrects_version": None,
                                                        "reason": None}
    final.sort(key=lambda e: (e["__t"], e["event_id"], e["record_version"]))
    exact_ids = {id(e) for e in exact_src}
    out = []
    for e in final:
        out.append(e)
        if id(e) in exact_ids:
            d = copy.deepcopy(strip_internal(e))
            d["__defect"] = ("duplicate_sensor_event_exact", f"exact duplicate of {e['event_id']} v{e['record_version']}")
            out.append(d)
    return out


# =============================================================================
# 9. Row builders: work orders and maintenance history
# =============================================================================
def wo_row(wo: dict, st: dict, version: int) -> dict:
    closed = st["status"] == "Closed"
    dmin = None
    if st["ds"] and st["de"]:
        dmin = int(round((st["de"] - st["ds"]).total_seconds() / 60))
    row = {
        "work_order_id": wo["id"], "equipment_id": wo["equipment_id"], "wo_type": wo["wo_type"],
        "priority": wo["priority"], "failure_mode_code": wo["fm"], "failure_mode_desc": FAILURE_MODES[wo["fm"]][0],
        "description": wo["description"], "requested_by_role": wo["requested_by"],
        "assigned_technician_id": wo["technician"], "created_ts": wo["created"], "due_ts": wo["due"],
        "actual_start_ts": st["actual"], "downtime_start_ts": st["ds"], "downtime_end_ts": st["de"],
        "downtime_minutes": dmin,
        "downtime_type": ("unplanned" if wo["wo_type"] == "Corrective" else "planned") if st["ds"] else None,
        "closed_ts": st["closed"], "status": st["status"],
        "cancellation_reason": wo["cancel_reason"] if st["status"] == "Cancelled" else None,
        "root_cause_code": wo["rc"] if closed else None,
        "triggering_event_id": wo["triggering_event_id"],
        "labor_hours": wo.get("labor_hours") if closed else None,
        "labor_cost": wo.get("labor_cost") if closed else None,
        "parts_cost": wo.get("parts_cost") if closed else None,
        "currency": wo["currency"], "last_modified_ts": st["last_mod"], "record_version": version,
        "__defect": None, "__order": (wo["id"], version, 0),
    }
    return row


def build_work_order_rows(rng: random.Random, wos: list[dict], plan: dict) -> list[dict]:
    rows = []
    by_id = {}
    for wo in wos:
        final = wo_state(wo, SPAN_END)
        if wo["straddler"]:
            r1, r2 = wo_row(wo, wo_state(wo, CUTOFF), 1), wo_row(wo, final, 2)
            set_audit(r1, 1, "CMMS", "cmms_work_orders_extract", "csv", wo["id"])
            set_audit(r2, 2, "CMMS", "cmms_work_orders_extract", "csv", wo["id"])
            rows += [r1, r2]
        else:
            r = wo_row(wo, final, 1)
            set_audit(r, batch_of(wo["created"]), "CMMS", "cmms_work_orders_extract", "csv", wo["id"])
            rows.append(r)
            by_id[wo["id"]] = (wo, r)

    for wo_id, (wo, r) in by_id.items():
        if not wo["defect"]:
            continue
        rule, detail = wo["defect"]
        if rule == "overlapping_downtime":
            detail = f"downtime overlaps {wo['overlap_with']['id']} on the same asset"
        elif rule == "missing_equipment_key":
            r["equipment_id"] = wo["row_equipment_id"]
        elif rule == "closure_before_creation":
            r["closed_ts"] = wo["created"] - hours(rng.uniform(1, 240))
            detail = "closed_ts earlier than created_ts"
            if wo.get("closure_fix"):
                fix = dict(r)
                fix.update({"closed_ts": wo["close"], "record_version": 2, "__defect": None,
                            "last_modified_ts": rdt(rng, CUTOFF + hours(2), SPAN_END - hours(2)),
                            "__order": (wo_id, 2, 0)})
                set_audit(fix, 2, "CMMS", "cmms_work_orders_extract", "csv", wo_id)
                rows.append(fix)
                detail += " (corrected by record_version 2)"
        elif rule == "out_of_range_value":
            v = rng.choice(["neg_labor", "huge_labor", "neg_parts", "neg_labor_cost"])
            if v == "neg_labor":
                r["labor_hours"], detail = -abs(r["labor_hours"]), "labor_hours negative"
            elif v == "huge_labor":
                r["labor_hours"], detail = 9999.0, "labor_hours implausible (9999)"
            elif v == "neg_parts":
                r["parts_cost"], detail = -abs(r["parts_cost"] or 0) - 10.0, "parts_cost negative"
            else:
                r["labor_cost"], detail = -abs(r["labor_cost"]), "labor_cost negative"
        r["__defect"] = (rule, detail)

    for wo_id, (wo, r) in by_id.items():  # priority corrections (clean v2 rows)
        if wo.get("priority_fix"):
            new_p = wo["priority_fix"]
            fix = dict(r)
            fix.update({"priority": new_p, "due_ts": wo["created"] + hours(SLA_HOURS[new_p]),
                        "record_version": 2, "__defect": None, "__order": (wo_id, 2, 0),
                        "last_modified_ts": rdt(rng, CUTOFF + hours(2), SPAN_END - hours(2))})
            set_audit(fix, 2, "CMMS", "cmms_work_orders_extract", "csv", wo_id)
            rows.append(fix)

    dup_pool = [r for wo, r in by_id.values() if r["__defect"] is None and not wo.get("closure_fix")
                and not wo.get("priority_fix")]
    for src in rng.sample(dup_pool, plan["duplicate_record"]):
        d = dict(src)
        d["__order"] = src["__order"][:2] + (1,)
        d["__defect"] = ("duplicate_record", "exact duplicate row")
        rows.append(d)
    rows.sort(key=lambda r: r["__order"])
    return rows


def build_maintenance_rows(rng: random.Random, acts: list[dict], plan: dict) -> list[dict]:
    acts.sort(key=lambda a: (a["start"], a["wo"]["id"] if a["wo"] else ""))
    rows = []
    for i, a in enumerate(acts, start=1):
        wo = a["wo"]
        r = {
            "maintenance_id": f"MH-{i:05d}", "work_order_id": wo["id"] if wo else None,
            "equipment_id": a["equipment_id"], "activity_start_ts": a["start"], "activity_end_ts": a["end"],
            **{k: a[k] for k in ("activity_type", "component", "component_code", "failure_mode_code",
                                 "root_cause_code", "detection_method", "action_description", "parts_qty",
                                 "parts_cost_eur", "labor_hours", "technician_id", "outcome", "next_due_date")},
            "last_modified_ts": a["end"] + timedelta(minutes=30), "record_version": 1,
            "__defect": None, "__order": (i, 1, 0), "__legacy": wo is None, "__wo": wo,
        }
        prefix = "legacy_cmms_history_export" if wo is None else "cmms_maintenance_history_extract"
        set_audit(r, batch_of(r["last_modified_ts"]), a["system"], prefix, "csv", r["maintenance_id"])
        rows.append(r)

    used: set = set()
    # Missing keys: rows of WOs with unknown EQ-9999 inherit it, rest are null.
    inherited = [r for r in rows if r["__wo"] and r["__wo"].get("row_equipment_id") == UNKNOWN_EQUIPMENT_ID]
    for r in inherited:
        r["equipment_id"] = UNKNOWN_EQUIPMENT_ID
        r["__defect"] = ("missing_equipment_key", "unknown equipment_id EQ-9999 inherited from work order")
        used.add(id(r))
    remaining = plan["missing_equipment_key"] - len(inherited)
    if remaining < 0:
        raise RuntimeError("too many inherited unknown keys")
    n_legacy = min(10, remaining)
    legacy = [r for r in rows if r["__legacy"]]
    normal = [r for r in rows if not r["__legacy"] and not (r["__wo"] and r["__wo"]["defect"]
                                                            and r["__wo"]["defect"][0] == "missing_equipment_key")]
    for r in take(rng, legacy, n_legacy, used, "legacy null key") + \
            take(rng, normal, remaining - n_legacy, used, "mh null key"):
        r["equipment_id"] = None
        r["__defect"] = ("missing_equipment_key", "equipment_id null" +
                         (" (legacy row, no work order: unresolvable)" if r["__legacy"]
                          else " (resolvable via work_order_id)"))

    pool = [r for r in normal if r["labor_hours"] > 0]
    for r in take(rng, pool, plan["closure_before_creation"], used, "mh closure"):
        r["activity_end_ts"] = r["activity_start_ts"] - hours(rng.uniform(0.5, 6))
        r["__defect"] = ("closure_before_creation", "activity_end_ts earlier than activity_start_ts")
    for r in take(rng, pool, plan["out_of_range_value"], used, "mh out of range"):
        v = rng.choice(["neg_labor", "huge_labor", "neg_qty", "neg_cost"])
        if v == "neg_labor":
            r["labor_hours"], detail = -r["labor_hours"], "labor_hours negative"
        elif v == "huge_labor":
            r["labor_hours"], detail = 999.0, "labor_hours implausible (999)"
        elif v == "neg_qty":
            r["parts_qty"], detail = -max(1, r["parts_qty"]), "parts_qty negative"
        else:
            r["parts_cost_eur"], detail = -abs(r["parts_cost_eur"]) - 5.0, "parts_cost_eur negative"
        r["__defect"] = ("out_of_range_value", detail)
    for r in take(rng, pool, plan["unit_inconsistency"], used, "mh unit"):
        r["labor_hours"] = round(r["labor_hours"] * 60, 1)
        r["__defect"] = ("unit_inconsistency", "labor_hours recorded in minutes")

    # Corrected records (record_version 2, clean, batch 2).
    corr_pool = [r for r in normal if r["batch_id"] == BATCHES[1]["batch_id"]]
    new_rows = []
    for r in take(rng, corr_pool, N_MH_CORRECTIONS, used, "mh corrections"):
        c = dict(r)
        opts = ["technician", "description"]
        if r["component_code"] == "BRG-DE":
            opts.append("component")
        if r["root_cause_code"]:
            opts.append("root_cause")
        v = rng.choice(opts)
        if v == "technician":
            site = r["__wo"]["asset"]["site_code"]
            c["technician_id"] = rng.choice([t for t in TECHNICIANS[site] if t != r["technician_id"]])
        elif v == "description":
            c["action_description"] = r["action_description"] + " (record completed by supervisor)"
        elif v == "component":
            c["component_code"], c["component"] = "BRG-NDE", COMPONENTS["BRG-NDE"][0]
        else:
            fm = r["failure_mode_code"]
            c["root_cause_code"] = rng.choice([k for k in RC_WEIGHTS[fm] if k != r["root_cause_code"]]
                                              or [r["root_cause_code"]])
        c.update({"record_version": 2, "last_modified_ts": rdt(rng, CUTOFF + hours(2), SPAN_END - hours(2)),
                  "__order": (r["__order"][0], 2, 0), "__defect": None})
        set_audit(c, 2, "CMMS", "cmms_maintenance_history_extract", "csv", c["maintenance_id"])
        new_rows.append(c)
    for src in take(rng, normal, plan["duplicate_record"], used, "mh duplicates"):
        d = dict(src)
        d["__order"] = src["__order"][:2] + (1,)
        d["__defect"] = ("duplicate_record", "exact duplicate row")
        new_rows.append(d)
    rows += new_rows
    rows.sort(key=lambda r: r["__order"])
    return rows


# =============================================================================
# 10. Writers
# =============================================================================
def fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return iso(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-0") else s
    return str(v)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in columns])


def json_ready(e: dict) -> dict:
    out = strip_internal(e)
    return out


def write_json_array(path: Path, events: list[dict]) -> None:
    """JSON array, one event per line (read in Spark with multiLine=true)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("[\n")
        f.write(",\n".join(json.dumps(json_ready(e), ensure_ascii=False) for e in events))
        f.write("\n]\n")


def manifest_rows(file_name: str, rows: list[dict], key_col: str, batch_getter) -> list[dict]:
    out = []
    for i, r in enumerate(rows):
        if r.get("__defect"):
            rule, detail = r["__defect"]
            out.append({"file": file_name, "row_index": i, "batch_id": batch_getter(r),
                        "record_key": r.get(key_col) if key_col != "event_id" else r["event_id"],
                        "record_version": r["record_version"], "rule": rule, "defect_detail": detail,
                        "expected_silver_handling": EXPECTED_HANDLING[rule]})
    return out


def write_reference_files(out: Path) -> None:
    ref = out / "reference"
    write_csv(ref / "failure_modes.csv",
              ["failure_mode_code", "failure_mode_desc", "iso14224_failure_mode", "failure_mechanism",
               "applicable_equipment_types"],
              [{"failure_mode_code": k, "failure_mode_desc": v[0], "iso14224_failure_mode": v[1],
                "failure_mechanism": v[2],
                "applicable_equipment_types": "|".join(t for t, s in EQUIPMENT_TYPES.items()
                                                       if k in s["failure_modes"]) or None}
               for k, v in FAILURE_MODES.items()])
    write_csv(ref / "root_causes.csv", ["root_cause_code", "root_cause_desc", "cause_category"],
              [{"root_cause_code": k, "root_cause_desc": v[0], "cause_category": v[1]}
               for k, v in ROOT_CAUSES.items()])
    write_csv(ref / "components.csv",
              ["component_code", "component_name", "component_family", "unit_cost_eur_min", "unit_cost_eur_max"],
              [{"component_code": k, "component_name": v[0], "component_family": v[1],
                "unit_cost_eur_min": float(v[2][0]), "unit_cost_eur_max": float(v[2][1])}
               for k, v in COMPONENTS.items()])
    write_csv(ref / "fx_rates.csv", ["rate_month", "currency", "rate_to_eur"],
              [{"rate_month": m, "currency": c, "rate_to_eur": r} for (c, m), r in sorted(FX_RATES.items(),
                                                                                           key=lambda x: (x[0][1], x[0][0]))])
    rows = []
    for etype, spec in EQUIPMENT_TYPES.items():
        for m in spec["measures"]:
            fam = MEASURES[m]["family"]
            row = {"equipment_type": etype, "measure": m, "sensor_type": fam, "canonical_unit": MEASURES[m]["unit"],
                   "alarm_code": MEASURES[m]["alarm_code"], "limits_version": "v1.0", "valid_from": "2026-01-01"}
            if fam == "vibration":
                row.update(limit_basis="absolute", warning_high=VIB_WARNING[spec["vib_alarm"]],
                           alarm_high=spec["vib_alarm"], range_min=0.0, range_max=50.0)
            elif fam == "temperature":
                row.update(limit_basis="absolute", warning_high=spec["temp_alarm"] - 10.0,
                           alarm_high=float(spec["temp_alarm"]), range_min=-40.0, range_max=250.0)
            elif fam == "pressure":
                a = spec["pressure"][0]
                row.update(limit_basis="absolute", warning_high=round(0.92 * a, 1), alarm_high=a,
                           range_min=0.0, range_max=round(2 * a, 1))
            elif fam == "current":
                row.update(limit_basis="pct_of_full_load_current", warning_high=105.0, alarm_high=115.0,
                           range_min=0.0, range_max=300.0)
            elif fam == "speed":
                row.update(limit_basis="pct_of_rated_speed", range_min=0.0, range_max=125.0)
            else:
                row.update(limit_basis="absolute", range_min=0.0, range_max=150.0)
            rows.append(row)
    write_csv(out / "config" / "sensor_limits.csv",
              ["equipment_type", "measure", "sensor_type", "canonical_unit", "limit_basis", "warning_high",
               "alarm_high", "range_min", "range_max", "alarm_code", "limits_version", "valid_from"], rows)


# =============================================================================
# 11. Self-check (re-derives defects from the written files)
# =============================================================================
def self_check(out: Path, manifest: list[dict]) -> list[str]:
    msgs, ok = [], True
    m_counts = Counter((m["file"], m["rule"]) for m in manifest)

    def read(name: str) -> list[dict]:
        with (out / name).open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def ts(s: str) -> datetime | None:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None

    eq, wo, mh = read("equipment_2.csv"), read("work_orders_2.csv"), read("maintenance_history_2.csv")
    se = json.loads((out / "sensor_events_2.json").read_text(encoding="utf-8"))
    for name, rows in [("equipment_2.csv", eq), ("work_orders_2.csv", wo), ("maintenance_history_2.csv", mh),
                       ("sensor_events_2.json", se)]:
        n_def = sum(v for (f, _), v in m_counts.items() if f == name)
        good = len(rows) == ROWS_PER_FILE and n_def == N_DEFECTS
        ok &= good
        msgs.append(f"{name:<28} rows={len(rows):>6}  defects={n_def:>4} ({n_def / len(rows):.1%})"
                    f"  {'OK' if good else 'MISMATCH'}")

    checks = []
    # Overlapping downtime on latest WO versions (distinct rows).
    latest = {}
    for r in wo:
        if r["work_order_id"] not in latest or int(r["record_version"]) > int(latest[r["work_order_id"]]["record_version"]):
            latest[r["work_order_id"]] = r
    by_eq = defaultdict(list)
    for r in latest.values():
        if r["equipment_id"] not in ("", UNKNOWN_EQUIPMENT_ID) and r["downtime_start_ts"] and r["downtime_end_ts"]:
            by_eq[r["equipment_id"]].append((ts(r["downtime_start_ts"]), ts(r["downtime_end_ts"])))
    overlaps = 0
    for iv in by_eq.values():
        iv.sort()
        end = None
        for s, e in iv:
            if end is not None and s < end:
                overlaps += 1
            end = e if end is None else max(end, e)
    checks.append(("work_orders overlapping downtime", overlaps, m_counts[("work_orders_2.csv", "overlapping_downtime")]))
    checks.append(("work_orders closed_ts < created_ts",
                   sum(1 for r in wo if r["closed_ts"] and ts(r["closed_ts"]) < ts(r["created_ts"])),
                   m_counts[("work_orders_2.csv", "closure_before_creation")]))
    checks.append(("work_orders missing/unknown equipment_id",
                   sum(1 for r in wo if r["equipment_id"] in ("", UNKNOWN_EQUIPMENT_ID)),
                   m_counts[("work_orders_2.csv", "missing_equipment_key")]))
    keys = Counter(tuple(r.values()) for r in wo)
    checks.append(("work_orders exact duplicate rows", sum(c - 1 for c in keys.values()),
                   m_counts[("work_orders_2.csv", "duplicate_record")]))
    checks.append(("maintenance end < start",
                   sum(1 for r in mh if ts(r["activity_end_ts"]) < ts(r["activity_start_ts"])),
                   m_counts[("maintenance_history_2.csv", "closure_before_creation")]))
    checks.append(("maintenance missing/unknown equipment_id",
                   sum(1 for r in mh if r["equipment_id"] in ("", UNKNOWN_EQUIPMENT_ID)),
                   m_counts[("maintenance_history_2.csv", "missing_equipment_key")]))
    checks.append(("equipment null equipment_id", sum(1 for r in eq if not r["equipment_id"]),
                   m_counts[("equipment_2.csv", "missing_equipment_key")]))
    checks.append(("equipment decommission < install",
                   sum(1 for r in eq if r["decommission_date"] and r["decommission_date"] < r["install_date"]),
                   m_counts[("equipment_2.csv", "closure_before_creation")]))
    raw = Counter(json.dumps(e, sort_keys=True) for e in se)
    checks.append(("sensor exact duplicates", sum(c - 1 for c in raw.values()),
                   m_counts[("sensor_events_2.json", "duplicate_sensor_event_exact")]))
    near = defaultdict(set)
    for e in se:
        near[(e["equipment_id"], e["sensor"]["sensor_id"], e["event_ts"])].add(e["event_id"])
    checks.append(("sensor near duplicates (new event_id)", sum(len(v) - 1 for v in near.values()),
                   m_counts[("sensor_events_2.json", "duplicate_sensor_event_near")]))
    checks.append(("sensor non-canonical units",
                   sum(1 for e in se if e["reading"]["unit"] != e["sensor"]["canonical_unit"]),
                   m_counts[("sensor_events_2.json", "unit_inconsistency")]))
    checks.append(("sensor missing/unknown equipment_id",
                   sum(1 for e in se if e["equipment_id"] in (None, UNKNOWN_EQUIPMENT_ID)),
                   m_counts[("sensor_events_2.json", "missing_equipment_key")]))
    oor_v1 = sum(1 for e in se if e["record_version"] == 1
                 and e["reading"]["unit"] == e["sensor"]["canonical_unit"] and not
                 (e["sensor"]["range_min"] <= e["reading"]["value"] <= e["sensor"]["range_max"]))
    checks.append(("sensor out-of-range (v1 rows)", oor_v1,
                   m_counts[("sensor_events_2.json", "out_of_range_reading")]))
    msgs.append("")
    for name, found, expected in checks:
        good = found == expected
        ok &= good
        msgs.append(f"  {name:<44} found={found:>4} manifest={expected:>4}  {'OK' if good else 'MISMATCH'}")
    if not ok:
        raise AssertionError("Self-check failed:\n" + "\n".join(msgs))
    return msgs


# =============================================================================
# 12. Orchestration
# =============================================================================
def generate(out: Path, seed: int) -> None:
    rng = random.Random(seed)
    eq_plan = split_evenly(N_DEFECTS, EQUIPMENT_RULES)
    wo_plan = split_evenly(N_DEFECTS, WORK_ORDER_RULES)
    mh_plan = split_evenly(N_DEFECTS, MAINTENANCE_RULES)
    se_plan = split_evenly(N_DEFECTS, SENSOR_RULES)

    # --- equipment -----------------------------------------------------------
    n_assets = ROWS_PER_FILE - N_EQUIPMENT_VERSIONS - eq_plan["duplicate_record"]
    assets = build_assets(rng, n_assets)
    monitored = choose_monitored(rng, assets)
    mon_idx = {a["idx"] for a in monitored}
    orphan_pool = [a for a in assets if a["idx"] not in mon_idx and a["decommission_date"] is None]
    orphans = {a["idx"] for a in rng.sample(orphan_pool, eq_plan["missing_equipment_key"])}
    assign_parents(rng, assets, excluded=orphans)
    for a in assets:
        a["limits"] = asset_limits(a)

    # --- monitored assets: degradation episodes + their work orders ---------
    episodes, mon_wos = {}, []
    for a in monitored:
        eps, wos = plan_monitored_asset(rng, a)
        episodes[a["equipment_id"]] = eps
        mon_wos += wos
    windows = {a["equipment_id"]: sorted([w for w in mon_wos if w["has_dt"] and w["asset"] is a],
                                         key=lambda w: w["ds"]) for a in monitored}

    # --- generic work orders -------------------------------------------------
    pool = [a for a in assets if a["idx"] not in mon_idx and a["idx"] not in orphans]
    gen_wos = build_generic_wos(rng, pool, WO_CANDIDATES - len(mon_wos))
    remove_overlaps(rng, gen_wos)

    # --- sensor readings (clean) and links to work orders -------------------
    n_clean = (ROWS_PER_FILE - se_plan["duplicate_sensor_event_exact"]
               - se_plan["duplicate_sensor_event_near"] - N_SENSOR_CORRECTIONS)
    events = build_clean_events(rng, monitored, episodes, windows, n_clean)
    link_sensor_evidence(rng, mon_wos, events)

    # --- work-order defects that change timelines, then trim to 10,000 ------
    inject_overlapping_downtime(rng, gen_wos, wo_plan["overlapping_downtime"])
    wos = mon_wos + gen_wos
    for w in wos:
        s1, s2 = wo_state(w, CUTOFF), wo_state(w, SPAN_END)
        w["straddler"] = s1 is not None and s1 != s2
    n_versions = sum(w["straddler"] for w in wos) + N_WO_CLOSURE_FIXES + N_WO_PRIORITY_FIXES
    excess = len(wos) + n_versions + wo_plan["duplicate_record"] - ROWS_PER_FILE
    removable = [w for w in gen_wos if not w["protected"] and not w["straddler"] and w["defect"] is None]
    if excess < 0 or excess > len(removable):
        raise RuntimeError(f"cannot trim work orders to {ROWS_PER_FILE} (excess={excess}); adjust WO_CANDIDATES")
    drop = {id(w) for w in rng.sample(removable, excess)}
    wos = [w for w in wos if id(w) not in drop]

    wos.sort(key=lambda w: (w["created"], w["equipment_id"], w["wo_type"]))
    for i, w in enumerate(wos):
        w["id"] = f"WO-{26001 + i}"
        w["description"] = describe(w)
    for i, w in enumerate(wos):
        if w["cancel_at"] is not None:
            earlier = [x["id"] for x in wos[max(0, i - 50):i] if x["equipment_id"] == w["equipment_id"]]
            w["cancel_reason"] = rng.choice(
                ([f"Duplicate of {earlier[-1]}"] if earlier else []) +
                ["Asset not available - production priority", "Scope merged into next PM", "Raised in error"])

    def final_closed_before(w: dict, limit: datetime) -> bool:
        st = wo_state(w, SPAN_END)
        return st is not None and st["status"] == "Closed" and w["close"] < limit

    wo_pool = [w for w in wos if not w["monitored"] and not w["protected"] and not w["straddler"]
               and w["defect"] is None and final_closed_before(w, CUTOFF - timedelta(days=2))]
    used: set = set()
    for i, w in enumerate(take(rng, wo_pool, wo_plan["missing_equipment_key"], used, "wo missing key")):
        unknown = i % 2 == 1
        w["row_equipment_id"] = UNKNOWN_EQUIPMENT_ID if unknown else None
        w["defect"] = ("missing_equipment_key", "unknown equipment_id EQ-9999" if unknown else
                       "equipment_id null (resolvable via maintenance_history)")
    closure = take(rng, wo_pool, wo_plan["closure_before_creation"], used, "wo closure")
    for w in closure:
        w["defect"] = ("closure_before_creation", None)
    for w in rng.sample(closure, N_WO_CLOSURE_FIXES):
        w["closure_fix"] = True
    for w in take(rng, wo_pool, wo_plan["out_of_range_value"], used, "wo out of range"):
        w["defect"] = ("out_of_range_value", None)
    prio_pool = [w for w in wo_pool if w["wo_type"] != "Corrective"]
    for w in take(rng, prio_pool, N_WO_PRIORITY_FIXES, used, "wo priority fix"):
        w["priority_fix"] = "P3" if w["priority"] == "P4" else "P4"

    # --- maintenance activities (exact row budget) ---------------------------
    eligible = [w for w in wos if wo_state(w, SPAN_END)["status"] == "Closed"]
    target = ROWS_PER_FILE - N_LEGACY_ACTIVITIES - N_MH_CORRECTIONS - mh_plan["duplicate_record"]
    extra = target - len(eligible)
    if extra < 0:
        raise RuntimeError("more closed work orders than activity rows; lower WO_CANDIDATES")
    k = {id(w): 1 for w in eligible}
    weight = {"Corrective": 3.0, "Predictive": 2.0, "Preventive": 1.5}
    cand2 = [w for w in eligible if w["wo_type"] in weight]
    second = weighted_sample(rng, cand2, [weight[w["wo_type"]] for w in cand2], min(extra, len(cand2)))
    for w in second:
        k[id(w)] = 2
    extra -= len(second)
    cand3 = [w for w in second if w["wo_type"] in ("Corrective", "Predictive", "Preventive")]
    for w in weighted_sample(rng, cand3, [weight[w["wo_type"]] for w in cand3], extra):
        k[id(w)] = 3
    acts = []
    for w in eligible:
        wa = build_activities(rng, w, k[id(w)])
        w["labor_hours"] = round(sum(a["labor_hours"] for a in wa), 2)
        parts_eur = sum(a["parts_cost_eur"] for a in wa)
        rate = FX_RATES[(w["currency"], w["created"].strftime("%Y-%m"))]
        w["labor_cost"] = round(w["labor_hours"] * LABOR_RATE_EUR / rate, 2)
        w["parts_cost"] = round(parts_eur / rate, 2)
        acts += wa
    acts += build_legacy_activities(rng, pool, N_LEGACY_ACTIVITIES)

    # --- rows -----------------------------------------------------------------
    eq_rows = build_equipment_rows(rng, assets, orphans, mon_idx, eq_plan)
    wo_rows = build_work_order_rows(rng, wos, wo_plan)
    mh_rows = build_maintenance_rows(rng, acts, mh_plan)
    se_rows = finalize_sensor_events(rng, events, se_plan)

    # --- write ------------------------------------------------------------------
    out.mkdir(parents=True, exist_ok=True)
    files = [("equipment_2.csv", EQUIPMENT_COLUMNS, eq_rows), ("work_orders_2.csv", WORK_ORDER_COLUMNS, wo_rows),
             ("maintenance_history_2.csv", MAINTENANCE_COLUMNS, mh_rows)]
    manifest = []
    for name, cols, rows in files:
        write_csv(out / name, cols, rows)
        key = cols[0]
        manifest += manifest_rows(name, rows, key, lambda r: r["batch_id"])
        for b in (1, 2):
            write_csv(out / "batches" / f"batch_0{b}" / name, cols,
                      [r for r in rows if r["batch_id"] == BATCHES[b]["batch_id"]])
    write_json_array(out / "sensor_events_2.json", se_rows)
    manifest += manifest_rows("sensor_events_2.json", se_rows, "event_id", lambda e: e["_metadata"]["batch_id"])
    for b in (1, 2):
        write_json_array(out / "batches" / f"batch_0{b}" / "sensor_events_2.json",
                         [e for e in se_rows if e["_metadata"]["batch_id"] == BATCHES[b]["batch_id"]])
    write_csv(out / "defect_manifest.csv", ["file", "row_index", "batch_id", "record_key", "record_version", "rule",
                                            "defect_detail", "expected_silver_handling"], manifest)
    write_reference_files(out)

    # --- summary ----------------------------------------------------------------
    report = self_check(out, manifest)
    final_states = Counter(wo_state(w, SPAN_END)["status"] for w in wos)
    summary = {
        "seed": seed, "span_start": iso(SPAN_START), "span_end_exclusive": iso(SPAN_END), "batch_cutoff": iso(CUTOFF),
        "rows_per_file": ROWS_PER_FILE, "defect_rate": DEFECT_RATE,
        "defect_plan": {"equipment_2.csv": eq_plan, "work_orders_2.csv": wo_plan,
                        "maintenance_history_2.csv": mh_plan, "sensor_events_2.json": se_plan},
        "batch_rows": {name: Counter(r["batch_id"] for r in rows) for name, _, rows in files} |
                      {"sensor_events_2.json": Counter(e["_metadata"]["batch_id"] for e in se_rows)},
        "assets": len(assets), "iot_monitored_assets": sorted(a["equipment_id"] for a in monitored),
        "work_orders_unique": len(wos), "work_order_status": dict(final_states),
        "work_orders_by_type": dict(Counter(w["wo_type"] for w in wos)),
        "repeat_failures": sum(w["is_repeat"] for w in wos),
        "predictive_with_trigger": sum(1 for w in wos if w["triggering_event_id"]),
        "late_sensor_events": N_LATE_EVENTS, "sensor_corrections": N_SENSOR_CORRECTIONS,
        "sensor_alarm_states": dict(Counter(e["alarm"]["state"] for e in se_rows)),
    }
    (out / "generation_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("\n".join(report))
    print(f"\nWork orders: {dict(final_states)} | repeats={summary['repeat_failures']} "
          f"| predictive with trigger={summary['predictive_with_trigger']}")
    print(f"Output written to {out.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("data"), help="output directory")
    parser.add_argument("--seed", type=int, default=SEED, help="random seed (reproducible output)")
    args = parser.parse_args()
    generate(args.out, args.seed)


if __name__ == "__main__":
    main()
