"""Per-technology reporting hooks for wind turbine."""

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
    """Scalar reporting metrics for the wind turbine block."""
    T = ctx["T"]
    NODES = ctx["NODES"]
    if not hasattr(block, "wind_generation") or not hasattr(block, "wind_potential"):
        return {}
    profiles = list(block.WIND)
    gen = 0.0
    max_kwh = 0.0
    for n in NODES:
        for p in profiles:
            cap = float(pyo.value(block.existing_wind_capacity[n, p]))
            if hasattr(block, "wind_capacity_adopted"):
                cap += float(pyo.value(block.wind_capacity_adopted[n, p]))
            for t in T:
                pot = float(pyo.value(block.wind_potential[n, p, t]))
                g = float(pyo.value(block.wind_generation[n, p, t]))
                gen += g
                max_kwh += cap * pot
    if max_kwh <= 0:
        cf: dict[str, Any] = {
            "value": None,
            "definition": "sum(wind_generation) / sum_{n,p,t}(installed_kw_np * wind_potential_npt)",
            "note": "zero_capacity_or_potential",
        }
    else:
        cf = {
            "value": gen / max_kwh,
            "definition": "sum(wind_generation) / sum_{n,p,t}(installed_kw_np * wind_potential_npt)",
            "generation_kwh": gen,
            "max_possible_kwh_if_at_potential": max_kwh,
        }
    return {"capacity_factor": cf}
