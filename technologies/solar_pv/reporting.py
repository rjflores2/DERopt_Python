"""Per-technology reporting hooks for solar PV.

Discovered by ``utilities.reporting.overarching_template`` via
``blk._technology_module`` (set by ``model.core``).

Hooks (all optional):
 - ``collect_block_report(model, block, data, ctx) -> dict``
 - ``collect_block_timeseries(model, block, data, ctx) -> dict[str, list[float]]``
 - ``collect_block_emissions(model, block, data, ctx) -> dict``
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
    """Scalar reporting metrics for the solar PV block.

    Emits ``capacity_factor`` = expected(solar_generation) / expected(installed_kw * solar_potential),
    where the expectation is probability-weighted across ``ctx["SCENARIOS"]`` (two-stage
    stochastic; capacity itself is Stage-1 and shared across scenarios, only generation and
    resource potential vary by scenario).
    """
    T = ctx["T"]
    NODES = ctx["NODES"]
    SCENARIOS = ctx["SCENARIOS"]
    scenario_probability = ctx["scenario_probability"]
    if not hasattr(block, "solar_generation") or not hasattr(block, "solar_potential"):
        return {}
    profiles = list(block.SOLAR)
    gen = 0.0
    max_kwh = 0.0
    for n in NODES:
        for p in profiles:
            cap = float(pyo.value(block.existing_solar_capacity[n, p]))
            if hasattr(block, "solar_capacity_adopted"):
                cap += float(pyo.value(block.solar_capacity_adopted[n, p]))
            for s in SCENARIOS:
                prob = float(pyo.value(scenario_probability[s]))
                for t in T:
                    pot = float(pyo.value(block.solar_potential[n, p, s, t]))
                    g = float(pyo.value(block.solar_generation[n, p, s, t]))
                    gen += prob * g
                    max_kwh += prob * cap * pot
    if max_kwh <= 0:
        cf: dict[str, Any] = {
            "value": None,
            "definition": "expected(solar_generation) / expected_{n,p,t}(installed_kw_np * solar_potential_npt)",
            "note": "zero_capacity_or_potential",
        }
    else:
        cf = {
            "value": gen / max_kwh,
            "definition": "expected(solar_generation) / expected_{n,p,t}(installed_kw_np * solar_potential_npt)",
            "generation_kwh": gen,
            "max_possible_kwh_if_at_potential": max_kwh,
        }
    return {"capacity_factor": cf}
