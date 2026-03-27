"""Demand-charge calendar and OpenEI/URDB schedule indexing (no Pyomo).

Maps simulation timesteps into billing year/month buckets and TOU demand tiers
for building peak-envelope constraints on ``grid_import_power_kw``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def rate_from_urdb_structure(struct: Any) -> float:
    """Best-effort extract of ``rate`` from OpenEI/URDB (possibly nested) structures.

    Common shapes:
    - [[{"rate": 12.3}]]  (tiered lists)
    - [{"rate": 12.3}]
    - {"rate": 12.3}
    """
    if struct is None:
        return 0.0
    if isinstance(struct, dict):
        return float(struct.get("rate", 0) or 0.0)
    if isinstance(struct, list) and struct:
        first = struct[0]
        return rate_from_urdb_structure(first)
    return 0.0


def tier_index_for_tou_demand_charge(dt: Any, demand_charges: dict[str, Any]) -> int:
    """Return demand-charge tier index for ``dt`` using 12×24 weekday/weekend schedules."""
    wd = demand_charges["demand_charge_weekdayschedule"]
    we = demand_charges["demand_charge_weekendschedule"]
    month = dt.month - 1
    hour = dt.hour
    is_weekend = dt.weekday() >= 5
    sched = we if is_weekend else wd
    n_tiers = len(demand_charges.get("demand_charge_ratestructure") or [])
    if month < len(sched) and hour < len(sched[month]):
        return min(sched[month][hour], max(0, n_tiers - 1))
    return 0


def times_by_year_month_from_datetimes(
    datetimes: list[Any | None],
    time_indices: list[int],
) -> dict[tuple[int, int], list[int]]:
    """Map ``(year, month_index)`` (month_index 0..11) to timestep indices with a valid datetime."""
    out: dict[tuple[int, int], list[int]] = {}
    for t in time_indices:
        if t >= len(datetimes):
            continue
        dt = datetimes[t]
        if dt is None:
            continue
        key = (dt.year, dt.month - 1)
        out.setdefault(key, []).append(t)
    return out


def sorted_year_month_keys(
    times_by_year_month: dict[tuple[int, int], list[int]],
) -> list[tuple[int, int]]:
    """Sorted distinct ``(year, month_index)`` keys present in the run."""
    return sorted(times_by_year_month.keys())


def flat_demand_nodes_and_rates_for_month(
    nodes: list[str],
    rates_by_node: dict[str, Any | None],
    month_index: int,
) -> tuple[list[str], dict[str, float]]:
    """Nodes with flat (or both) demand charges applicable in ``month_index``, and their $/kW rates."""
    flat_nodes: list[str] = []
    flat_rate_by_node: dict[str, float] = {}
    for n in nodes:
        utility_rate_for_node = rates_by_node.get(n)
        demand_charges = (
            getattr(utility_rate_for_node, "demand_charges", None)
            if utility_rate_for_node is not None
            else None
        )
        if not demand_charges or demand_charges.get("demand_charge_type") not in ("flat", "both"):
            continue
        applicable = set(demand_charges.get("flat_demand_charge_applicable_months") or [])
        if applicable and month_index not in applicable:
            continue
        flat_struct = demand_charges.get("flat_demand_charge_structure") or [[]]
        flat_month_map = demand_charges.get("flat_demand_charge_months") or []
        struct_idx = 0
        if month_index < len(flat_month_map):
            try:
                struct_idx = int(flat_month_map[month_index])
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Node {n!r}: flat_demand_charge_months[{month_index}] must be an int structure index; "
                    f"got {flat_month_map[month_index]!r}"
                ) from e
        if not isinstance(flat_struct, list) or not flat_struct:
            raise ValueError(f"Node {n!r}: flat_demand_charge_structure must be a non-empty list")
        if struct_idx < 0 or struct_idx >= len(flat_struct):
            raise ValueError(
                f"Node {n!r}: flat_demand_charge_months[{month_index}] selects structure index {struct_idx} out of range "
                f"for flat_demand_charge_structure (len={len(flat_struct)})"
            )
        flat_nodes.append(n)
        flat_rate_by_node[n] = rate_from_urdb_structure(flat_struct[struct_idx])
    return flat_nodes, flat_rate_by_node


@dataclass(frozen=True)
class TouDemandTierGroup:
    """One TOU demand tier within a calendar month: nodes, timesteps, and rates ($/kW)."""

    tier_index: int
    tier_nodes: list[str]
    times_by_node: dict[str, list[int]]
    rate_by_node: dict[str, float]


def tou_demand_tier_groups_for_month(
    month_times: list[int],
    datetimes: list[Any | None],
    nodes: list[str],
    rates_by_node: dict[str, Any | None],
) -> list[TouDemandTierGroup]:
    """Group ``(node, t)`` by TOU tier for one ``(year, month)``; sorted by tier index."""
    times_by_tier_node: dict[int, dict[str, list[int]]] = {}
    rate_by_tier_node: dict[tuple[int, str], float] = {}
    for n in nodes:
        utility_rate_for_node = rates_by_node.get(n)
        demand_charges = (
            getattr(utility_rate_for_node, "demand_charges", None)
            if utility_rate_for_node is not None
            else None
        )
        if not demand_charges or demand_charges.get("demand_charge_type") not in ("tou", "both"):
            continue
        drs = demand_charges.get("demand_charge_ratestructure") or []
        for t in month_times:
            if t >= len(datetimes) or datetimes[t] is None:
                continue
            ti = tier_index_for_tou_demand_charge(datetimes[t], demand_charges)
            tier = drs[ti] if ti < len(drs) else {}
            rate_by_tier_node[(ti, n)] = rate_from_urdb_structure(tier)
            times_by_tier_node.setdefault(ti, {}).setdefault(n, []).append(t)

    groups: list[TouDemandTierGroup] = []
    for ti in sorted(times_by_tier_node.keys()):
        by_node = times_by_tier_node[ti]
        tier_nodes = sorted(by_node.keys())
        if not tier_nodes:
            continue
        node_rates = {n: rate_by_tier_node[(ti, n)] for n in tier_nodes}
        groups.append(
            TouDemandTierGroup(
                tier_index=ti,
                tier_nodes=tier_nodes,
                times_by_node={n: by_node[n] for n in tier_nodes},
                rate_by_node=node_rates,
            )
        )
    return groups
