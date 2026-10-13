"""A tiny knowledge graph in the shape of the Gold tables `gold_kg_nodes` / `gold_kg_edges` (rows as load_tables() returns
them). Shared by the unit tests (in-memory backend) and the Neo4j contract test in tests/integration.

    EQ-1 (Pump) WO-1 -> WO-2 -> WO-3   WO-2 repeats WO-1 (BRG-WEAR, 5.5 days); WO-3 is an OVERHEAT
    EQ-2 (Pump) WO-4 -> WO-6           BRG-WEAR then OVERHEAT after 2 days
    EQ-3 (Motor) WO-5 -> WO-7          BRG-WEAR then OVERHEAT after 8 days
"""

import json

TS = "2026-10-13 12:51:45.077914"


def _node(node_type, key, label=None, **props):
    return {"node_id": f"{node_type}:{key}", "node_type": node_type, "business_key": key, "label": label or key,
            "properties": json.dumps(props) if props else None}


def _edge(edge_type, src, dst, weight=1.0, **props):
    return {"src_id": src, "dst_id": dst, "edge_type": edge_type, "weight": weight,
            "properties": json.dumps(props) if props else None}


NODES = [
    _node("Site", "S1", "Plant 1"),
    _node("EquipmentType", "Pump"), _node("EquipmentType", "Motor"),
    _node("Equipment", "EQ-1", "Pump 1"), _node("Equipment", "EQ-2", "Pump 2"), _node("Equipment", "EQ-3", "Motor 1"),
    _node("FailureMode", "BRG-WEAR", "Bearing wear"), _node("FailureMode", "OVERHEAT", "Overheating"),
    *[_node("WorkOrder", f"WO-{i}", created_ts=f"2026-0{i}-01T08:00:00.000Z") for i in range(1, 8)],
]

EDGES = [
    _edge("IS_OF_TYPE", "Equipment:EQ-1", "EquipmentType:Pump"),
    _edge("IS_OF_TYPE", "Equipment:EQ-2", "EquipmentType:Pump"),
    _edge("IS_OF_TYPE", "Equipment:EQ-3", "EquipmentType:Motor"),
    *[_edge("PERFORMED_ON", f"WorkOrder:WO-{wo}", f"Equipment:{eq}")
      for wo, eq in [(1, "EQ-1"), (2, "EQ-1"), (3, "EQ-1"), (4, "EQ-2"), (6, "EQ-2"), (5, "EQ-3"), (7, "EQ-3")]],
    _edge("EXPERIENCED_FAILURE", "Equipment:EQ-1", "FailureMode:BRG-WEAR", 2.0,
          downtime_hours=11.5, repeat_failures=1, last_failure_ts="2026-02-01T08:00:00.000Z"),
    _edge("EXPERIENCED_FAILURE", "Equipment:EQ-1", "FailureMode:OVERHEAT", 1.0,
          downtime_hours=3.0, repeat_failures=0, last_failure_ts="2026-03-01T08:00:00.000Z"),
    _edge("TYPE_PRONE_TO", "EquipmentType:Pump", "FailureMode:BRG-WEAR", 3.0, equipment_affected=2, downtime_hours=14.5),
    _edge("TYPE_PRONE_TO", "EquipmentType:Pump", "FailureMode:OVERHEAT", 2.0, equipment_affected=2, downtime_hours=6.0),
    _edge("TYPE_PRONE_TO", "EquipmentType:Motor", "FailureMode:OVERHEAT", 1.0, equipment_affected=1, downtime_hours=2.0),
    _edge("REPEATED_FAILURE", "WorkOrder:WO-2", "WorkOrder:WO-1", 5.5, failure_mode="BRG-WEAR"),
    _edge("FOLLOWED_BY", "WorkOrder:WO-1", "WorkOrder:WO-2", 5.5, from_mode="BRG-WEAR", to_mode="BRG-WEAR"),
    _edge("FOLLOWED_BY", "WorkOrder:WO-2", "WorkOrder:WO-3", 10.0, from_mode="BRG-WEAR", to_mode="OVERHEAT"),
    _edge("FOLLOWED_BY", "WorkOrder:WO-4", "WorkOrder:WO-6", 2.0, from_mode="BRG-WEAR", to_mode="OVERHEAT"),
    _edge("FOLLOWED_BY", "WorkOrder:WO-5", "WorkOrder:WO-7", 8.0, from_mode="BRG-WEAR", to_mode="OVERHEAT"),
]


def load_fixture():
    """Same return value as graph_tools.load_tables()."""
    return NODES, EDGES, TS
