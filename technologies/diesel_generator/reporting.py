"""Per-technology reporting hooks for diesel generators.

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
    """Scalar reporting metrics for the diesel block.

    Emits ``capacity_factor`` = expected_generation_kwh / (installed_kw * |T| * dt_hours),
    where expected generation is probability-weighted across ``ctx["SCENARIOS"]`` (two-stage
    stochastic; capacity itself is Stage-1 and shared across scenarios, only generation
    varies by scenario).
    """
    T = ctx["T"]
    NODES = ctx["NODES"]
    SCENARIOS = ctx["SCENARIOS"]
    scenario_probability = ctx["scenario_probability"]
    dt_hours = float(ctx.get("dt_hours") or 1.0)
    if not hasattr(block, "diesel_generation") or not hasattr(block, "installed_capacity"):
        return {}
    gen = float(
        sum(
            float(pyo.value(scenario_probability[s])) * pyo.value(block.diesel_generation[n, s, t])
            for n in NODES
            for s in SCENARIOS
            for t in T
        )
    )
    cap_sum = float(sum(pyo.value(block.installed_capacity[n]) for n in NODES))
    denom = cap_sum * len(T) * dt_hours
    if denom <= 0:
        cf: dict[str, Any] = {
            "value": None,
            "definition": "expected(diesel_generation) / (sum_installed_kw * |T| * dt_hours)",
            "note": "zero_capacity",
        }
    else:
        cf = {
            "value": gen / denom,
            "definition": "sum(diesel_generation_kwh) / (sum_installed_kw * horizon_kwh_at_nameplate)",
            "generation_kwh": gen,
            "installed_kw_sum": cap_sum,
        }
    return {"capacity_factor": cf}
