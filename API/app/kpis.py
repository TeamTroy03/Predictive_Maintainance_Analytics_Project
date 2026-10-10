"""The 12 governed KPIs: the same formulas as KPI_FORMULAS in Gold cell 3 (and the DAX).

kpi_equipment_month (alias k) holds additive pieces per equipment and month, so every
ratio is SUM(numerator) / SUM(denominator) and is correct at any grouping.
"""

from enum import StrEnum


class Kpi(StrEnum):
    availability = "availability"
    unplanned_downtime_hours = "unplanned_downtime_hours"
    unplanned_failures = "unplanned_failures"
    mtbf_hours = "mtbf_hours"
    mttr_hours = "mttr_hours"
    overdue_work_orders = "overdue_work_orders"
    alarm_rate = "alarm_rate"
    alarm_warning_rate = "alarm_warning_rate"
    alarm_critical_rate = "alarm_critical_rate"
    maintenance_cost_eur = "maintenance_cost_eur"
    repeat_failure_rate = "repeat_failure_rate"
    work_orders = "work_orders"


# kpi -> (unit, SQL formula, description)
KPI_DEFINITIONS: dict[Kpi, tuple[str, str, str]] = {
    Kpi.availability: (
        "ratio",
        "(SUM(k.calendar_hours) - SUM(k.downtime_hours)) / NULLIF(SUM(k.calendar_hours), 0)",
        "Share of calendar time the equipment was not down",
    ),
    Kpi.unplanned_downtime_hours: ("hours", "SUM(k.unplanned_downtime_hours)", "Unplanned downtime"),
    Kpi.unplanned_failures: ("count", "SUM(k.unplanned_failure_count)", "Number of unplanned failures"),
    Kpi.mtbf_hours: (
        "hours",
        "SUM(k.operating_uptime_hours) / NULLIF(SUM(k.unplanned_failure_count), 0)",
        "Mean time between failures",
    ),
    Kpi.mttr_hours: (
        "hours",
        "SUM(k.unplanned_downtime_hours) / NULLIF(SUM(k.unplanned_failure_count), 0)",
        "Mean time to repair",
    ),
    Kpi.overdue_work_orders: (
        "count",
        "SUM(k.overdue_work_order_count)",
        "Open work orders past due (as-of snapshot)",
    ),
    Kpi.alarm_rate: (
        "per asset-day",
        "SUM(k.alarm_count) / NULLIF(SUM(k.asset_days), 0)",
        "Warning or alarm readings per operating asset-day",
    ),
    Kpi.alarm_warning_rate: (
        "per asset-day",
        "SUM(k.alarm_warning_count) / NULLIF(SUM(k.asset_days), 0)",
        "Warning readings per operating asset-day",
    ),
    Kpi.alarm_critical_rate: (
        "per asset-day",
        "SUM(k.alarm_critical_count) / NULLIF(SUM(k.asset_days), 0)",
        "Alarm readings per operating asset-day",
    ),
    Kpi.maintenance_cost_eur: ("EUR", "SUM(k.maintenance_cost_eur)", "Labour plus parts cost"),
    Kpi.repeat_failure_rate: (
        "ratio",
        "SUM(k.repeat_failure_count) / NULLIF(SUM(k.unplanned_failure_count), 0)",
        "Failures repeating within 30 days",
    ),
    Kpi.work_orders: ("count", "SUM(k.work_order_count)", "Work orders created, excluding cancelled"),
}
