"""Per-technology reporting hooks for hydrokinetic generators.

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
    """Scalar reporting metrics for the hydrokinetic block.

    Emits ``capacity_factor`` = expected(hkt_generation) / (installed_kw * |T| * dt_hours),
    where expected generation is probability-weighted across ``ctx["SCENARIOS"]`` (two-stage
    stochastic; capacity itself is Stage-1 and shared across scenarios, only generation
    varies by scenario).
    """
    T = ctx["T"]
    NODES = ctx["NODES"]
    SCENARIOS = ctx["SCENARIOS"]
    scenario_probability = ctx["scenario_probability"]
    dt_hours = float(ctx.get("dt_hours") or 1.0)
    if not hasattr(block, "hkt_generation") or not hasattr(block, "total_capacity_kw"):
        return {}
    hkt_set = list(block.HKT)
    gen = 0.0
    cap_sum = 0.0
    for n in NODES:
        for h in hkt_set:
            cap_sum += float(pyo.value(block.total_capacity_kw[n, h]))
            for s in SCENARIOS:
                prob = float(pyo.value(scenario_probability[s]))
                for t in T:
                    gen += prob * float(pyo.value(block.hkt_generation[n, h, s, t]))
    denom = cap_sum * len(T) * dt_hours
    if denom <= 0:
        cf: dict[str, Any] = {
            "value": None,
            "definition": "expected(hkt_generation) / (sum_total_capacity_kw * |T| * dt_hours)",
            "note": "zero_capacity",
        }
    else:
        cf = {
            "value": gen / denom,
            "definition": "expected(hkt_generation_kwh) / (sum_installed_kw * horizon_kwh_at_nameplate)",
            "generation_kwh": gen,
            "installed_kw_sum": cap_sum,
        }
    return {"capacity_factor": cf}
