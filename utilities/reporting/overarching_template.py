"""Overarching solution report template: costs, emissions hook, energy mix, capacity factor.

This is the **stable cross-run shape** for comparing studies. Run-specific papers can add JSON sidecars, extra CSVs, or pass ``emissions_provider`` without changing the
core template keys.

**Sections**

- ``meta``: horizon, timestep, nodes
- ``costs``: aggregate (from objective + utility expressions) + per top-level block
- ``emissions``: placeholder until a carbon model exists; optional callback fills data
- ``energy_mix_kwh``: served load, grid imports, and per-block source/sink electricity
- ``capacity_factor``: where a sensible definition exists (solar, diesel, hydrokinetic)
"""

from __future__ import annotations

from typing import Any, Callable

import pyomo.environ as pyo

from utilities.results import extract_solution

SCHEMA_VERSION = 1

EmissionsProvider = Callable[[Any, Any], dict[str, Any]]


def _sum_source_kwh(model: Any, blk: Any, T: list, NODES: list) -> float:
    if not hasattr(blk, "electricity_source_term"):
        return 0.0
    total = 0.0
    for n in NODES:
        for t in T:
            v = pyo.value(blk.electricity_source_term[n, t], exception=False)
            if v is not None:
                total += float(v)
    return total


def _sum_sink_kwh(model: Any, blk: Any, T: list, NODES: list) -> float:
    if not hasattr(blk, "electricity_sink_term"):
        return 0.0
    total = 0.0
    for n in NODES:
        for t in T:
            v = pyo.value(blk.electricity_sink_term[n, t], exception=False)
            if v is not None:
                total += float(v)
    return total


def _solar_capacity_factor(model: Any, data: Any, T: list, NODES: list) -> dict[str, Any] | None:
    if not hasattr(model, "solar_pv"):
        return None
    b = model.solar_pv
    if not hasattr(b, "solar_generation") or not hasattr(b, "solar_potential"):
        return None
    nodes = list(NODES)
    profiles = list(b.SOLAR)
    gen = 0.0
    max_kwh = 0.0
    for n in nodes:
        for p in profiles:
            cap = float(pyo.value(b.existing_solar_capacity[n, p]))
            if hasattr(b, "solar_capacity_adopted"):
                cap += float(pyo.value(b.solar_capacity_adopted[n, p]))
            for t in T:
                pot = float(pyo.value(b.solar_potential[p, t]))
                g = float(pyo.value(b.solar_generation[n, p, t]))
                gen += g
                max_kwh += cap * pot
    if max_kwh <= 0:
        return {"value": None, "definition": "generation_kwh / sum_installed_kw_x_potential_kwh_per_kw", "note": "zero_capacity_or_potential"}
    return {
        "value": gen / max_kwh,
        "definition": "sum(solar_generation) / sum_{n,p,t}(installed_kw_np * solar_potential_pt)",
        "generation_kwh": gen,
        "max_possible_kwh_if_at_potential": max_kwh,
    }


def _diesel_capacity_factor(model: Any, T: list, NODES: list, dt_hours: float) -> dict[str, Any] | None:
    if not hasattr(model, "diesel_generator"):
        return None
    b = model.diesel_generator
    if not hasattr(b, "diesel_generation") or not hasattr(b, "installed_capacity"):
        return None
    gen = float(sum(pyo.value(b.diesel_generation[n, t]) for n in NODES for t in T))
    cap_sum = float(sum(pyo.value(b.installed_capacity[n]) for n in NODES))
    denom = cap_sum * len(T) * dt_hours
    if denom <= 0:
        return {"value": None, "definition": "sum(diesel_generation) / (sum_installed_kw * |T| * dt_hours)", "note": "zero_capacity"}
    return {
        "value": gen / denom,
        "definition": "sum(diesel_generation_kwh) / (sum_installed_kw * horizon_kwh_at_nameplate)",
        "generation_kwh": gen,
        "installed_kw_sum": cap_sum,
    }


def _hydrokinetic_capacity_factor(model: Any, T: list, NODES: list, dt_hours: float) -> dict[str, Any] | None:
    if not hasattr(model, "hydrokinetic"):
        return None
    b = model.hydrokinetic
    if not hasattr(b, "hkt_generation") or not hasattr(b, "total_capacity_kw"):
        return None
    nodes = list(NODES)
    hkt_set = list(b.HKT)
    gen = 0.0
    cap_sum = 0.0
    for n in nodes:
        for h in hkt_set:
            cap_sum += float(pyo.value(b.total_capacity_kw[n, h]))
            for t in T:
                gen += float(pyo.value(b.hkt_generation[n, h, t]))
    denom = cap_sum * len(T) * dt_hours
    if denom <= 0:
        return {"value": None, "definition": "sum(hkt_generation) / (sum_total_capacity_kw * |T| * dt_hours)", "note": "zero_capacity"}
    return {
        "value": gen / denom,
        "definition": "sum(hkt_generation_kwh) / (sum_installed_kw * horizon_kwh_at_nameplate)",
        "generation_kwh": gen,
        "installed_kw_sum": cap_sum,
    }


def build_overarching_report(
    model: Any,
    data: Any,
    *,
    extracted: dict[str, Any] | None = None,
    emissions_provider: EmissionsProvider | None = None,
) -> dict[str, Any]:
    """Build the overarching template dict (JSON-serializable).

    Parameters
    ----------
    model
        Solved Pyomo model from ``build_model``.
    data
        ``DataContainer`` used for the run.
    extracted
        Optional precomputed ``extract_solution`` dict; if None, computed here.
    emissions_provider
        Optional ``f(model, data) -> {"aggregate": {...}, "by_technology": {...}}``.
        If omitted, ``emissions.status`` is ``not_modeled``.
    """
    T = list(model.T)
    NODES = list(model.NODES)
    n_time = len(T)
    dt_hours = float(data.static.get("time_step_hours") or 1.0)

    raw = extracted if extracted is not None else extract_solution(model, data)
    ts = raw.get("timeseries") or {}

    load_total = float(sum(ts.get("load_kwh") or [0.0] * n_time))
    grid_total = float(sum(ts.get("grid_import_kwh") or [0.0] * n_time))

    by_block_costs: dict[str, dict[str, float]] = {}
    by_block_energy: dict[str, dict[str, float]] = {}

    for blk in model.component_objects(pyo.Block, descend_into=False):
        name = str(blk.name)
        row: dict[str, float] = {}
        if hasattr(blk, "objective_contribution"):
            row["cost_optimizing_annual"] = float(pyo.value(blk.objective_contribution))
        if hasattr(blk, "cost_non_optimizing_annual"):
            row["cost_non_optimizing_annual"] = float(pyo.value(blk.cost_non_optimizing_annual))
        if row:
            by_block_costs[name] = row

        erow: dict[str, float] = {}
        sk = _sum_source_kwh(model, blk, T, NODES)
        if sk > 0:
            erow["electricity_source_kwh"] = sk
        sink = _sum_sink_kwh(model, blk, T, NODES)
        if sink > 0:
            erow["electricity_sink_kwh"] = sink
        if erow:
            by_block_energy[name] = erow

    agg_costs = dict(raw.get("cost_breakdown") or {})
    if raw.get("objective_value") is not None:
        agg_costs.setdefault("objective_optimizing_annual", raw["objective_value"])

    if emissions_provider is not None:
        emissions_body = emissions_provider(model, data)
        emissions = {"status": "provided", **emissions_body}
    else:
        emissions = {
            "status": "not_modeled",
            "aggregate": {},
            "by_technology": {},
            "note": "Pass emissions_provider(model, data) or extend post-processing for CO2e / air permits.",
        }

    energy_aggregate = {
        "load_kwh": load_total,
        "grid_import_kwh": grid_total,
    }
    if by_block_energy:
        energy_aggregate["behind_the_meter_generation_kwh"] = float(
            sum(v.get("electricity_source_kwh", 0.0) for v in by_block_energy.values())
        )

    cf: dict[str, Any] = {}
    s_cf = _solar_capacity_factor(model, data, T, NODES)
    if s_cf is not None:
        cf["solar_pv"] = s_cf
    d_cf = _diesel_capacity_factor(model, T, NODES, dt_hours)
    if d_cf is not None:
        cf["diesel_generator"] = d_cf
    hk_cf = _hydrokinetic_capacity_factor(model, T, NODES, dt_hours)
    if hk_cf is not None:
        cf["hydrokinetic"] = hk_cf

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "n_timesteps": n_time,
            "time_step_hours": dt_hours,
            "nodes": list(NODES),
        },
        "costs": {
            "aggregate": agg_costs,
            "by_technology": by_block_costs,
        },
        "emissions": emissions,
        "energy_mix_kwh": {
            "aggregate": energy_aggregate,
            "by_technology": by_block_energy,
        },
        "capacity_factor": {
            "by_technology": cf,
            "note": "Battery/flow-battery capacity factor is intentionally omitted; use charge/discharge totals in energy_mix.",
        },
    }
