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

    Emits ``capacity_factor`` = generation_kwh / (installed_kw * |T| * dt_hours).
    """
    T = ctx["T"]
    NODES = ctx["NODES"]
    dt_hours = float(ctx.get("dt_hours") or 1.0)
    if not hasattr(block, "hkt_generation") or not hasattr(block, "total_capacity_kw"):
        return {}
    hkt_set = list(block.HKT)
    gen = 0.0
    cap_sum = 0.0
    for n in NODES:
        for h in hkt_set:
            cap_sum += float(pyo.value(block.total_capacity_kw[n, h]))
            for t in T:
                gen += float(pyo.value(block.hkt_generation[n, h, t]))
    denom = cap_sum * len(T) * dt_hours
    if denom <= 0:
        cf: dict[str, Any] = {
            "value": None,
            "definition": "sum(hkt_generation) / (sum_total_capacity_kw * |T| * dt_hours)",
            "note": "zero_capacity",
        }
    else:
        cf = {
            "value": gen / denom,
            "definition": "sum(hkt_generation_kwh) / (sum_installed_kw * horizon_kwh_at_nameplate)",
            "generation_kwh": gen,
            "installed_kw_sum": cap_sum,
        }
    return {"capacity_factor": cf}
