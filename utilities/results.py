"""Extract and report optimization results.

After solving, use extract_solution() to get a structured dict, then
print_solution_summary() for terminal output and/or write_timeseries_csv() for export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyomo.environ as pyo


def extract_solution(model: Any, data: Any) -> dict[str, Any]:
    """Extract solution from a solved model into a plain dict.

    Returns:
        objective_value: float (optimization objective; decision-relevant costs only)
        cost_breakdown: dict including optimizing_cost, fixed_non_optimizing_cost,
            total_reported_cost, plus utility component slices when available
        timeseries: dict with per-timestep arrays (length = len(T)):
            grid_import_kwh, load_kwh, solar_kwh (if solar block present), import_price_per_kwh, datetime (if in data)
    """
    T = list(model.T)
    NODES = list(model.NODES)
    n_time = len(T)

    out: dict[str, Any] = {
        "objective_value": None,
        "cost_breakdown": {},
        "timeseries": {
            "grid_import_kwh": [0.0] * n_time,
            "load_kwh": [0.0] * n_time,
            "solar_kwh": [0.0] * n_time,
            "import_price_per_kwh": [],
            "datetime": [],
        },
    }

    if model.obj.is_constructed():
        out["objective_value"] = float(pyo.value(model.obj))
        out["cost_breakdown"]["optimizing_cost"] = float(pyo.value(model.obj))
    if hasattr(model, "total_cost_non_optimizing_annual"):
        out["cost_breakdown"]["fixed_non_optimizing_cost"] = float(pyo.value(model.total_cost_non_optimizing_annual))
    if (
        "optimizing_cost" in out["cost_breakdown"]
        and "fixed_non_optimizing_cost" in out["cost_breakdown"]
    ):
        out["cost_breakdown"]["total_reported_cost"] = (
            out["cost_breakdown"]["optimizing_cost"]
            + out["cost_breakdown"]["fixed_non_optimizing_cost"]
        )

    # Per-block cost slices for downstream decomposition/reporting.
    non_opt_components: dict[str, float] = {}
    optimizing_components: dict[str, float] = {}
    for blk in model.component_objects(pyo.Block, descend_into=False):
        name = str(blk.name)
        if hasattr(blk, "cost_non_optimizing_annual"):
            non_opt_components[name] = float(pyo.value(blk.cost_non_optimizing_annual))
        if hasattr(blk, "objective_contribution"):
            optimizing_components[name] = float(pyo.value(blk.objective_contribution))
    if non_opt_components:
        out["cost_breakdown"]["non_optimizing_components"] = non_opt_components
    if optimizing_components:
        out["cost_breakdown"]["optimizing_components"] = optimizing_components

    # Utility block: cost components and grid_import
    if hasattr(model, "utility"):
        ub = model.utility
        if hasattr(ub, "energy_import_cost"):
            out["cost_breakdown"]["energy_import_cost"] = float(pyo.value(ub.energy_import_cost))
        if hasattr(ub, "nonTOU_Demand_Charge_Cost"):
            out["cost_breakdown"]["nonTOU_demand_charge_cost"] = float(pyo.value(ub.nonTOU_Demand_Charge_Cost))
        if hasattr(ub, "TOU_Demand_Charge_Cost"):
            out["cost_breakdown"]["TOU_demand_charge_cost"] = float(pyo.value(ub.TOU_Demand_Charge_Cost))
        if hasattr(ub, "grid_import"):
            for t in T:
                out["timeseries"]["grid_import_kwh"][t] = sum(
                    float(pyo.value(ub.grid_import[n, t])) for n in NODES
                )
        if hasattr(ub, "import_price"):
            try:
                out["timeseries"]["import_price_per_kwh"] = [
                    float(sum(pyo.value(ub.import_price[n, t]) for n in NODES) / max(1, len(NODES)))
                    for t in T
                ]
            except Exception:
                out["timeseries"]["import_price_per_kwh"] = [float(pyo.value(ub.import_price[t])) for t in T]

    # Load from model param
    if hasattr(model, "electricity_load"):
        for t in T:
            out["timeseries"]["load_kwh"][t] = sum(
                float(pyo.value(model.electricity_load[n, t])) for n in NODES
            )

    # Solar from block electricity_source_term (sum over nodes)
    if hasattr(model, "solar_pv") and hasattr(model.solar_pv, "electricity_source_term"):
        for t in T:
            out["timeseries"]["solar_kwh"][t] = sum(
                float(pyo.value(model.solar_pv.electricity_source_term[n, t])) for n in NODES
            )

    # Datetime from data (optional)
    datetimes = getattr(data, "timeseries", {}).get("datetime") if data else None
    if datetimes is not None and len(datetimes) == n_time:
        out["timeseries"]["datetime"] = list(datetimes)
    else:
        out["timeseries"]["datetime"] = list(range(n_time))

    # If we never filled import_price from utility block, use data.import_prices
    if not out["timeseries"]["import_price_per_kwh"] and getattr(data, "import_prices", None):
        out["timeseries"]["import_price_per_kwh"] = list(data.import_prices)

    return out


def print_solution_summary(extracted: dict[str, Any]) -> None:
    """Print a short summary of the solution to the terminal."""
    obj = extracted.get("objective_value")
    if obj is not None:
        print(f"  Objective (decision-relevant cost): {obj:,.2f}")
    cb = extracted.get("cost_breakdown") or {}
    if cb:
        print("  Cost breakdown:")
        for k, v in cb.items():
            if v is not None and v != 0:
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
