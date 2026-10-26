"""Per-technology reporting hooks for controllable hydro.

Discovered by ``utilities.reporting.overarching_template`` via
``blk._technology_module`` (set by ``model.core``).
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer


def collect_block_report(
    model: pyo.Block,
    block: pyo.Block,
    data: DataContainer,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Scalar reporting metrics for the controllable_hydro block.

    ``total_spill_kwh`` / ``spill_fraction`` are the primary diagnostic for whether the
    reservoir is oversubscribed. ``pump_energy_kwh`` / ``round_trip_efficiency`` are only
    emitted where any unit has non-zero pump capacity (conventional-dam-only cases have no
    pumping to report).
    """
    T = ctx["T"]
    NODES = ctx["NODES"]
    SCENARIOS = ctx["SCENARIOS"]
    scenario_probability = ctx["scenario_probability"]
    dt_hours = float(ctx.get("dt_hours") or 1.0)
    if not hasattr(block, "turbine_generation") or not hasattr(block, "reservoir_capacity_kwh"):
        return {}

    units = list(block.HYDRO)

    gen_kwh = 0.0
    spill_kwh = 0.0
    pump_kwh = 0.0
    inflow_kwh = 0.0
    turbine_cap_sum = 0.0
    pump_cap_sum = 0.0
    min_inventory_fraction: float | None = None
    max_inventory_fraction: float | None = None

    for n in NODES:
        for d in units:
            turbine_cap_sum += float(pyo.value(block.turbine_capacity_kw[n, d]))
            pump_cap_sum += float(pyo.value(block.pump_capacity_kw[n, d]))
            capacity = float(pyo.value(block.reservoir_capacity_kwh[n, d]))
            for s in SCENARIOS:
                prob = float(pyo.value(scenario_probability[s]))
                for t in T:
                    gen_kwh += prob * float(pyo.value(block.turbine_generation[n, d, s, t]))
                    spill_kwh += prob * float(pyo.value(block.spill[n, d, s, t]))
                    pump_kwh += prob * float(pyo.value(block.pump_consumption[n, d, s, t]))
                    inflow_kwh += prob * float(pyo.value(block.inflow_kwh[d, s, t]))
                    if capacity > 0:
                        frac = float(pyo.value(block.reservoir_inventory[n, d, s, t])) / capacity
                        if min_inventory_fraction is None or frac < min_inventory_fraction:
                            min_inventory_fraction = frac
                        if max_inventory_fraction is None or frac > max_inventory_fraction:
                            max_inventory_fraction = frac

    denom = turbine_cap_sum * len(T) * dt_hours
    if denom <= 0:
        turbine_capacity_factor: dict[str, Any] = {
            "value": None,
            "definition": "expected(turbine_generation) / (sum_turbine_kw * |T| * dt_hours)",
            "note": "zero_capacity",
        }
    else:
        turbine_capacity_factor = {
            "value": gen_kwh / denom,
            "definition": "expected(turbine_generation) / (sum_turbine_kw * horizon_kwh_at_nameplate)",
            "generation_kwh": gen_kwh,
            "turbine_capacity_kw_sum": turbine_cap_sum,
        }

    report: dict[str, Any] = {
        "turbine_capacity_factor": turbine_capacity_factor,
        "total_spill_kwh": spill_kwh,
        "spill_fraction": (spill_kwh / inflow_kwh) if inflow_kwh > 0 else None,
        "minimum_inventory_reached": min_inventory_fraction,
        "maximum_inventory_reached": max_inventory_fraction,
    }

    if pump_cap_sum > 0:
        report["pump_energy_kwh"] = pump_kwh
        report["round_trip_efficiency"] = float(pyo.value(block.pump_efficiency)) * float(
            pyo.value(block.turbine_efficiency)
        )

    return report
