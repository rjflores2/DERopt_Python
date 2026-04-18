"""Overarching solution report: costs, energy mix, per-tech reports / timeseries / emissions.

This template is the **stable cross-run shape** for comparing studies. It is built from:

 1. A **generic block walk** that collects contract fields
    (``objective_contribution``, ``cost_non_optimizing_annual``) and registered
    scalar ``pyo.Expression`` components on each top-level ``pyo.Block``.
 2. A **generic timeseries walk** that sums ``electricity_source_term`` /
    ``electricity_sink_term`` over nodes for each block exposing them.
 3. **Per-technology plugin hooks** discovered via ``blk._technology_module``
    (set by ``model.core``). Each module may define any of:
       - ``collect_block_report(model, block, data, ctx) -> dict``
       - ``collect_block_timeseries(model, block, data, ctx) -> dict[str, list[float]]``
       - ``collect_block_emissions(model, block, data, ctx) -> dict``
    Missing modules / missing hooks are silently skipped (no every tech needs them).

Callers can still pass a monolithic ``emissions_provider=f(model, data)`` which
**overrides** the per-tech aggregation and fills the ``emissions`` section directly.

Sections
--------
- ``meta``: horizon, timestep, nodes
- ``costs``: aggregate (from objective + non-optimizing totals) and per top-level block
- ``emissions``: per-technology / per-node, or ``status == "not_modeled"`` placeholder
- ``energy_mix_kwh``: served load, grid imports, and per-block source/sink electricity
- ``timeseries``: generic per-block source/sink arrays plus any tech-specific series
- ``by_technology_report``: free-form per-tech scalars (e.g. capacity factors)
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

SCHEMA_VERSION = 2

EmissionsProvider = Callable[[pyo.Block, DataContainer], dict[str, Any]]

# Hook name conventions (match diagnostics plugin style).
_HOOK_REPORT = "collect_block_report"
_HOOK_TIMESERIES = "collect_block_timeseries"
_HOOK_EMISSIONS = "collect_block_emissions"


def _load_reporting_hooks(technology_module: str) -> dict[str, Callable]:
    """Import ``<technology_module>.reporting`` and return any hooks it defines.

    Missing modules or missing hook names yield an empty dict (silent skip).
    """
    hooks: dict[str, Callable] = {}
    try:
        mod = importlib.import_module(f"{technology_module}.reporting")
    except ImportError:
        return hooks
    for name in (_HOOK_REPORT, _HOOK_TIMESERIES, _HOOK_EMISSIONS):
        fn = getattr(mod, name, None)
        if callable(fn):
            hooks[name] = fn
    return hooks


def _sum_source_kwh(blk: pyo.Block, T: list, NODES: list) -> float:
    if not hasattr(blk, "electricity_source_term"):
        return 0.0
    total = 0.0
    for n in NODES:
        for t in T:
            v = pyo.value(blk.electricity_source_term[n, t], exception=False)
            if v is not None:
                total += float(v)
    return total


def _sum_sink_kwh(blk: pyo.Block, T: list, NODES: list) -> float:
    if not hasattr(blk, "electricity_sink_term"):
        return 0.0
    total = 0.0
    for n in NODES:
        for t in T:
            v = pyo.value(blk.electricity_sink_term[n, t], exception=False)
            if v is not None:
                total += float(v)
    return total


def _source_timeseries(blk: pyo.Block, T: list, NODES: list) -> list[float] | None:
    if not hasattr(blk, "electricity_source_term"):
        return None
    series = [0.0] * len(T)
    for idx, t in enumerate(T):
        s = 0.0
        for n in NODES:
            v = pyo.value(blk.electricity_source_term[n, t], exception=False)
            if v is not None:
                s += float(v)
        series[idx] = s
    return series


def _sink_timeseries(blk: pyo.Block, T: list, NODES: list) -> list[float] | None:
    if not hasattr(blk, "electricity_sink_term"):
        return None
    series = [0.0] * len(T)
    for idx, t in enumerate(T):
        s = 0.0
        for n in NODES:
            v = pyo.value(blk.electricity_sink_term[n, t], exception=False)
            if v is not None:
                s += float(v)
        series[idx] = s
    return series


def _load_timeseries(model: pyo.Block, T: list, NODES: list) -> list[float]:
    series = [0.0] * len(T)
    if not hasattr(model, "electricity_load"):
        return series
    for idx, t in enumerate(T):
        series[idx] = float(sum(pyo.value(model.electricity_load[n, t]) for n in NODES))
    return series


def _scalar_expressions(blk: pyo.Block) -> dict[str, float]:
    """Return scalar ``pyo.Expression`` values on ``blk``, excluding contract expressions."""
    out: dict[str, float] = {}
    for expr_comp in blk.component_objects(pyo.Expression, descend_into=False):
        expr_name = str(expr_comp.local_name)
        if expr_name in ("objective_contribution", "cost_non_optimizing_annual"):
            continue
        if expr_comp.is_indexed():
            continue
        out[expr_name] = float(pyo.value(expr_comp))
    return out


def build_overarching_report(
    model: pyo.Block,
    data: DataContainer,
    *,
    emissions_provider: EmissionsProvider | None = None,
    emission_factors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the overarching template dict (JSON-serializable).

    Parameters
    ----------
    model
        Solved Pyomo model from ``build_model``.
    data
        ``DataContainer`` used for the run.
    emissions_provider
        Optional monolithic callback ``f(model, data) -> {"aggregate": {...}, "by_technology": {...}}``.
        If provided, it overrides per-tech ``collect_block_emissions`` aggregation.
    emission_factors
        Optional mapping passed through ``ctx["emission_factors"]`` to per-tech
        ``collect_block_emissions`` hooks (e.g. grid CO2e/kWh by region or by node).
    """
    T = list(model.T)
    NODES = list(model.NODES)
    n_time = len(T)
    dt_hours = float(data.static.get("time_step_hours") or 1.0)

    ctx: dict[str, Any] = {
        "T": T,
        "NODES": NODES,
        "dt_hours": dt_hours,
        "emission_factors": emission_factors,
    }

    # --- Aggregate costs from the model's top-level reporting totals -------------
    agg_costs: dict[str, Any] = {}
    if hasattr(model, "obj") and model.obj.is_constructed():
        obj_value = float(pyo.value(model.obj))
        agg_costs["optimizing_cost"] = obj_value
        agg_costs["objective_optimizing_annual"] = obj_value
    if hasattr(model, "total_cost_non_optimizing_annual"):
        agg_costs["fixed_non_optimizing_cost"] = float(pyo.value(model.total_cost_non_optimizing_annual))
    if "optimizing_cost" in agg_costs and "fixed_non_optimizing_cost" in agg_costs:
        agg_costs["total_reported_cost"] = (
            agg_costs["optimizing_cost"] + agg_costs["fixed_non_optimizing_cost"]
        )

    # --- Per-block walks: costs, energy, scalar expressions, per-tech hooks ------
    by_block_costs: dict[str, dict[str, float]] = {}
    by_block_energy: dict[str, dict[str, float]] = {}
    by_block_source_ts: dict[str, list[float]] = {}
    by_block_sink_ts: dict[str, list[float]] = {}
    by_block_report: dict[str, dict[str, Any]] = {}
    by_block_tech_ts: dict[str, dict[str, list[float]]] = {}
    by_block_emissions: dict[str, dict[str, Any]] = {}

    for blk in model.component_objects(pyo.Block, descend_into=False):
        block_name = str(blk.local_name)

        # Cost row: contract scalars + any other scalar Expressions on the block.
        cost_row: dict[str, float] = {}
        if hasattr(blk, "objective_contribution"):
            cost_row["cost_optimizing_annual"] = float(pyo.value(blk.objective_contribution))
        if hasattr(blk, "cost_non_optimizing_annual"):
            cost_row["cost_non_optimizing_annual"] = float(pyo.value(blk.cost_non_optimizing_annual))
        cost_row.update(_scalar_expressions(blk))
        if cost_row:
            by_block_costs[block_name] = cost_row

        # Energy totals (aggregate kWh over horizon).
        erow: dict[str, float] = {}
        sk = _sum_source_kwh(blk, T, NODES)
        if sk > 0:
            erow["electricity_source_kwh"] = sk
        sink = _sum_sink_kwh(blk, T, NODES)
        if sink > 0:
            erow["electricity_sink_kwh"] = sink
        if erow:
            by_block_energy[block_name] = erow

        # Per-block source / sink timeseries (summed over nodes).
        src_ts = _source_timeseries(blk, T, NODES)
        if src_ts is not None and any(src_ts):
            by_block_source_ts[block_name] = src_ts
        snk_ts = _sink_timeseries(blk, T, NODES)
        if snk_ts is not None and any(snk_ts):
            by_block_sink_ts[block_name] = snk_ts

        # Per-tech plugin hooks (optional; discovered via ``blk._technology_module``).
        technology_module = getattr(blk, "_technology_module", None)
        if not technology_module:
            continue
        hooks = _load_reporting_hooks(technology_module)
        if not hooks:
            continue

        report_fn = hooks.get(_HOOK_REPORT)
        if report_fn is not None:
            out = report_fn(model, blk, data, ctx) or {}
            if out:
                by_block_report[block_name] = out

        ts_fn = hooks.get(_HOOK_TIMESERIES)
        if ts_fn is not None:
            out_ts = ts_fn(model, blk, data, ctx) or {}
            if out_ts:
                by_block_tech_ts[block_name] = {k: list(v) for k, v in out_ts.items()}

        emis_fn = hooks.get(_HOOK_EMISSIONS)
        if emis_fn is not None:
            out_em = emis_fn(model, blk, data, ctx) or {}
            if out_em:
                by_block_emissions[block_name] = out_em

    # --- Aggregates for energy_mix_kwh ----------------------------------------
    load_ts = _load_timeseries(model, T, NODES)
    load_total = float(sum(load_ts))
    grid_total = float(sum(by_block_source_ts.get("utility", [0.0] * n_time)))

    energy_aggregate = {
        "load_kwh": load_total,
        "grid_import_kwh": grid_total,
    }
    if by_block_energy:
        energy_aggregate["behind_the_meter_generation_kwh"] = float(
            sum(v.get("electricity_source_kwh", 0.0) for v in by_block_energy.values())
        )

    # --- Emissions section -----------------------------------------------------
    if emissions_provider is not None:
        body = emissions_provider(model, data) or {}
        emissions: dict[str, Any] = {"status": "provided", **body}
    elif by_block_emissions:
        # Aggregate per-tech emissions by unit key (e.g. ``co2e_kg``) across blocks.
        aggregate: dict[str, float] = {}
        for block_name, per_block in by_block_emissions.items():
            block_agg = per_block.get("aggregate") or per_block
            for unit_key, value in block_agg.items():
                if isinstance(value, (int, float)):
                    aggregate[unit_key] = aggregate.get(unit_key, 0.0) + float(value)
        emissions = {
            "status": "provided",
            "aggregate": aggregate,
            "by_technology": by_block_emissions,
        }
    else:
        emissions = {
            "status": "not_modeled",
            "aggregate": {},
            "by_technology": {},
            "note": "Pass emissions_provider, emission_factors, or define collect_block_emissions on a tech module.",
        }

    # --- Timeseries section ----------------------------------------------------
    timeseries: dict[str, Any] = {
        "load_kwh": load_ts,
        "by_block_electricity_source_kwh": by_block_source_ts,
        "by_block_electricity_sink_kwh": by_block_sink_ts,
    }
    if by_block_tech_ts:
        timeseries["by_block_technology_specific"] = by_block_tech_ts

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
        "timeseries": timeseries,
        "by_technology_report": by_block_report,
    }
