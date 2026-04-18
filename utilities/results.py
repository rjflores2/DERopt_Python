"""Extract and report optimization results.

``extract_solution`` is now a thin **legacy adapter** over
``utilities.reporting.build_overarching_report``: it flattens the canonical
overarching report into the flat ``objective_value`` / ``cost_breakdown`` /
``timeseries`` dict that ``run/playground.py`` and older tests rely on.

For new code, prefer calling ``build_overarching_report`` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer
from utilities.postsolve_diagnostics import (
    check_simultaneous_charge_discharge,
    format_simultaneous_charge_discharge_warnings,
)
from utilities.reporting import build_overarching_report


def extract_solution(model: pyo.ConcreteModel, data: DataContainer) -> dict[str, Any]:
    """Extract solution into the legacy flat dict shape.

    This is a view over ``build_overarching_report`` plus a few adapter-only
    additions (``import_price_per_kwh`` from the utility block's per-node import
    prices, ``datetime`` from ``data.timeseries``, and ``postsolve_diagnostics``).

    Returns:
        objective_value: float (optimization objective; decision-relevant costs only)
        cost_breakdown:
            - ``optimizing_cost``, ``fixed_non_optimizing_cost``, ``total_reported_cost``
            - ``optimizing_components`` / ``non_optimizing_components``: per-block scalar totals
            - ``by_block_expressions``: all other scalar Expressions on each block
              (e.g. utility's ``energy_import_cost``, ``nonTOU_Demand_Charge_Cost``, ``TOU_Demand_Charge_Cost``)
        timeseries: per-timestep arrays of length ``len(T)``:
            - ``grid_import_kwh``, ``solar_kwh``: legacy top-level aliases
              (populated from ``electricity_source_kwh_by_block``)
            - ``electricity_source_kwh_by_block``: dict keyed by block name, summed over nodes
            - ``load_kwh``, ``import_price_per_kwh``, ``datetime``
        by_technology_report: raw per-tech ``collect_block_report`` output
            (e.g. solar/diesel/hydrokinetic capacity factors)
        postsolve_diagnostics: simultaneous charge/discharge warnings, etc.
    """
    T = list(model.T)
    NODES = list(model.NODES)
    n_time = len(T)

    report = build_overarching_report(model, data)
    agg = report["costs"]["aggregate"]
    by_tech_cost = report["costs"]["by_technology"]

    cost_breakdown: dict[str, Any] = {}
    if "optimizing_cost" in agg:
        cost_breakdown["optimizing_cost"] = agg["optimizing_cost"]
    if "fixed_non_optimizing_cost" in agg:
        cost_breakdown["fixed_non_optimizing_cost"] = agg["fixed_non_optimizing_cost"]
    if "total_reported_cost" in agg:
        cost_breakdown["total_reported_cost"] = agg["total_reported_cost"]

    # Per-block decomposition: pivot the overarching report's by_technology cost rows
    # into the legacy ``optimizing_components`` / ``non_optimizing_components`` /
    # ``by_block_expressions`` dicts.
    optimizing_components: dict[str, float] = {}
    non_opt_components: dict[str, float] = {}
    by_block_expressions: dict[str, dict[str, float]] = {}
    for block_name, row in by_tech_cost.items():
        if "cost_optimizing_annual" in row:
            optimizing_components[block_name] = float(row["cost_optimizing_annual"])
        if "cost_non_optimizing_annual" in row:
            non_opt_components[block_name] = float(row["cost_non_optimizing_annual"])
        extra = {
            k: float(v)
            for k, v in row.items()
            if k not in ("cost_optimizing_annual", "cost_non_optimizing_annual")
        }
        if extra:
            by_block_expressions[block_name] = extra
    if optimizing_components:
        cost_breakdown["optimizing_components"] = optimizing_components
    if non_opt_components:
        cost_breakdown["non_optimizing_components"] = non_opt_components
    if by_block_expressions:
        cost_breakdown["by_block_expressions"] = by_block_expressions

    # Timeseries: start from the overarching report's per-block source arrays, then
    # surface legacy ``grid_import_kwh`` / ``solar_kwh`` aliases for the common blocks.
    by_block_source = dict(report["timeseries"].get("by_block_electricity_source_kwh") or {})
    timeseries: dict[str, Any] = {
        "grid_import_kwh": list(by_block_source.get("utility", [0.0] * n_time)),
        "solar_kwh": list(by_block_source.get("solar_pv", [0.0] * n_time)),
        "load_kwh": list(report["timeseries"].get("load_kwh") or [0.0] * n_time),
        "import_price_per_kwh": [],
        "datetime": [],
    }
    if by_block_source:
        timeseries["electricity_source_kwh_by_block"] = by_block_source

    # Utility import price is not part of the overarching schema (it's an input-facing
    # signal, not a solution output), so the adapter computes it directly.
    if hasattr(model, "utility") and hasattr(model.utility, "import_price"):
        ub = model.utility
        try:
            timeseries["import_price_per_kwh"] = [
                float(sum(pyo.value(ub.import_price[n, t]) for n in NODES) / max(1, len(NODES)))
                for t in T
            ]
        except Exception:
            timeseries["import_price_per_kwh"] = [float(pyo.value(ub.import_price[t])) for t in T]

    datetimes = getattr(data, "timeseries", {}).get("datetime") if data else None
    if datetimes is not None and len(datetimes) == n_time:
        timeseries["datetime"] = list(datetimes)
    else:
        timeseries["datetime"] = list(range(n_time))

    if not timeseries["import_price_per_kwh"] and getattr(data, "import_prices", None):
        timeseries["import_price_per_kwh"] = list(data.import_prices)

    return {
        "objective_value": agg.get("objective_optimizing_annual"),
        "cost_breakdown": cost_breakdown,
        "timeseries": timeseries,
        "by_technology_report": report.get("by_technology_report") or {},
        # Post-solve diagnostics: flag (but do not fix) storage devices that operate
        # charge + discharge simultaneously. Informational only.
        "postsolve_diagnostics": {
            "simultaneous_charge_discharge": check_simultaneous_charge_discharge(model),
        },
    }


def print_solution_summary(extracted: dict[str, Any]) -> None:
    """Print a short summary of the solution to the terminal."""
    obj = extracted.get("objective_value")
    if obj is not None:
        print(f"  Objective (decision-relevant cost): {obj:,.2f}")
    cb = extracted.get("cost_breakdown") or {}
    if cb:
        print("  Cost breakdown:")
        for k, v in cb.items():
            if v is None:
                continue
            if isinstance(v, dict):
                nz = {ik: iv for ik, iv in v.items() if not isinstance(iv, dict) and iv}
                if not nz and not any(isinstance(iv, dict) for iv in v.values()):
                    continue
                print(f"    {k}:")
                for ik, iv in v.items():
                    if isinstance(iv, dict):
                        inner = {nk: nv for nk, nv in iv.items() if nv}
                        if not inner:
                            continue
                        print(f"      {ik}:")
                        for nk, nv in inner.items():
                            print(f"        {nk}: {nv:,.2f}")
                    elif iv:
                        print(f"      {ik}: {iv:,.2f}")
            elif v:
                print(f"    {k}: {v:,.2f}")
    ts = extracted.get("timeseries") or {}
    grid = ts.get("grid_import_kwh") or []
    load = ts.get("load_kwh") or []
    solar = ts.get("solar_kwh") or []
    if grid:
        total_grid = sum(grid)
        total_load = sum(load) if load else 0
        total_solar = sum(solar) if solar else 0
        print("  Totals (kWh):")
        print(f"    Grid import: {total_grid:,.0f}")
        print(f"    Load:       {total_load:,.0f}")
        if total_solar:
            print(f"    Solar:      {total_solar:,.0f}")
        if grid and max(grid) > 0:
            print(f"    Peak grid import (kW): {max(grid):,.2f}")

    diagnostics = extracted.get("postsolve_diagnostics") or {}
    sim_report = diagnostics.get("simultaneous_charge_discharge") or {}
    sim_lines = format_simultaneous_charge_discharge_warnings(sim_report)
    if sim_lines:
        print("  Post-solve diagnostics:")
        for line in sim_lines:
            print(line)


def write_timeseries_csv(extracted: dict[str, Any], path: Path | str) -> None:
    """Write time series from extracted solution to a CSV file.

    Columns: datetime (or step), load_kwh, grid_import_kwh, solar_kwh, import_price_per_kwh.
    """
    path = Path(path)
    ts = extracted.get("timeseries") or {}
    n = len(ts.get("grid_import_kwh") or [])
    if n == 0:
        return
    datetimes = ts.get("datetime") or list(range(n))
    load = ts.get("load_kwh") or [0.0] * n
    grid = ts.get("grid_import_kwh") or [0.0] * n
    solar = ts.get("solar_kwh") or [0.0] * n
    price = ts.get("import_price_per_kwh") or [0.0] * n
    # Normalize lengths
    load = (load + [0.0] * n)[:n]
    grid = (grid + [0.0] * n)[:n]
    solar = (solar + [0.0] * n)[:n]
    price = (price + [0.0] * n)[:n]

    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("datetime,load_kwh,grid_import_kwh,solar_kwh,import_price_per_kwh\n")
        for i in range(n):
            dt = datetimes[i] if i < len(datetimes) else i
            if hasattr(dt, "isoformat"):
                dt_str = dt.isoformat()
            else:
                dt_str = str(dt)
            f.write(f"{dt_str},{load[i]:.4f},{grid[i]:.4f},{solar[i]:.4f},{price[i]:.6f}\n")
    print(f"  Wrote timeseries to {path}")
