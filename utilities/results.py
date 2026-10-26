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


def _sum(values) -> float:
    return float(sum(float(pyo.value(v)) for v in values))


def _extract_adopted_capacity(model: pyo.ConcreteModel) -> dict[str, dict[str, float]]:
    """Best-effort, per-technology installed/adopted capacity, summed over all nodes.

    Guarded with ``hasattr`` throughout: a technology not present on ``model``, or a
    formulation variant (LP vs. MILP/binary, adoption on vs. off) that doesn't define a
    given attribute, is silently skipped rather than raising -- this must work for any
    case/technology mix, not just the one it was written against.
    """
    nodes = list(model.NODES)
    out: dict[str, dict[str, float]] = {}

    if hasattr(model, "solar_pv"):
        blk = model.solar_pv
        profiles = list(blk.SOLAR)
        existing = _sum(blk.existing_solar_capacity[n, p] for n in nodes for p in profiles)
        adopted = (
            _sum(blk.solar_capacity_adopted[n, p] for n in nodes for p in profiles)
            if hasattr(blk, "solar_capacity_adopted")
            else 0.0
        )
        out["solar_pv"] = {"existing_kw": existing, "adopted_kw": adopted, "total_kw": existing + adopted}

    if hasattr(model, "hydrokinetic"):
        blk = model.hydrokinetic
        profiles = list(blk.HKT)
        existing = _sum(blk.existing_capacity_kw[n, p] for n in nodes for p in profiles)
        total = _sum(blk.total_capacity_kw[n, p] for n in nodes for p in profiles)
        entry = {"existing_kw": existing, "adopted_kw": total - existing, "total_kw": total}
        if hasattr(blk, "installed_units"):
            entry["installed_units"] = _sum(blk.installed_units[n, p] for n in nodes for p in profiles)
        out["hydrokinetic"] = entry

    if hasattr(model, "controllable_hydro"):
        blk = model.controllable_hydro
        units = list(blk.HYDRO)
        out["controllable_hydro"] = {
            "turbine_capacity_kw": _sum(blk.turbine_capacity_kw[n, d] for n in nodes for d in units),
            "pump_capacity_kw": _sum(blk.pump_capacity_kw[n, d] for n in nodes for d in units),
        }

    if hasattr(model, "battery_energy_storage"):
        blk = model.battery_energy_storage
        existing = _sum(blk.existing_energy_capacity[n] for n in nodes)
        total = _sum(blk.total_energy_capacity[n] for n in nodes)
        out["battery_energy_storage"] = {
            "existing_kwh": existing, "adopted_kwh": total - existing, "total_kwh": total,
        }

    if hasattr(model, "flow_battery_energy_storage"):
        blk = model.flow_battery_energy_storage
        if hasattr(blk, "total_energy_capacity"):
            out["flow_battery_energy_storage"] = {
                "total_kwh": _sum(blk.total_energy_capacity[n] for n in nodes),
            }

    for tech_name in ("pem_electrolyzer", "pem_fuel_cell", "alkaline_electrolyzer"):
        if not hasattr(model, tech_name):
            continue
        blk = getattr(model, tech_name)
        if not hasattr(blk, "installed_electric_capacity_kw"):
            continue
        existing = _sum(blk.existing_capacity_kw[n] for n in nodes)
        total = _sum(blk.installed_electric_capacity_kw[n] for n in nodes)
        entry = {"existing_kw": existing, "adopted_kw": total - existing, "total_kw": total}
        if hasattr(blk, "installed_unit_count"):
            entry["installed_units"] = _sum(blk.installed_unit_count[n] for n in nodes)
        out[tech_name] = entry

    if hasattr(model, "compressed_gas_hydrogen_storage"):
        blk = model.compressed_gas_hydrogen_storage
        existing = _sum(blk.existing_energy_capacity_kwh_h2_lhv[n] for n in nodes)
        total = _sum(blk.total_energy_capacity_kwh_h2_lhv[n] for n in nodes)
        out["compressed_gas_hydrogen_storage"] = {
            "existing_kwh_h2": existing, "adopted_kwh_h2": total - existing, "total_kwh_h2": total,
        }

    if hasattr(model, "diesel_generator"):
        blk = model.diesel_generator
        unit_kw = float(pyo.value(blk.unit_capacity_kw))
        if hasattr(blk, "installed_unit_count"):
            units = _sum(blk.installed_unit_count[n] for n in nodes)
            out["diesel_generator"] = {"installed_units": units, "installed_kw": units * unit_kw}
        elif hasattr(blk, "installed_capacity"):
            out["diesel_generator"] = {"installed_kw": _sum(blk.installed_capacity[n] for n in nodes)}

    return out


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
    # signal, not a solution output), so the adapter computes it directly. import_price is
    # [node, scenario, t]-indexed (two-stage stochastic); report the probability-weighted
    # expected price across scenarios, node-averaged, matching the rest of the reporting
    # layer's expected-value convention.
    if hasattr(model, "utility") and hasattr(model.utility, "import_price"):
        ub = model.utility
        scenarios = list(model.SCENARIOS)
        probs = {s: float(pyo.value(model.scenario_probability[s])) for s in scenarios}
        timeseries["import_price_per_kwh"] = [
            float(
                sum(
                    probs[s] * pyo.value(ub.import_price[n, s, t])
                    for n in NODES
                    for s in scenarios
                )
                / max(1, len(NODES))
            )
            for t in T
        ]

    datetimes = getattr(data, "timeseries", {}).get("datetime") if data else None
    if datetimes is not None and len(datetimes) == n_time:
        timeseries["datetime"] = list(datetimes)
    else:
        timeseries["datetime"] = list(range(n_time))

    if not timeseries["import_price_per_kwh"] and getattr(data, "import_prices", None):
        timeseries["import_price_per_kwh"] = list(data.import_prices)

    # LCOE = total reported annual cost / annual served load. Uses total_reported_cost
    # (optimizing + fixed non-optimizing) rather than just the optimization objective, since
    # LCOE is meant to be the full system cost per kWh served, not only the decision-relevant
    # portion of it.
    annual_load_kwh = sum(timeseries["load_kwh"])
    total_reported_cost = cost_breakdown.get("total_reported_cost")
    lcoe_per_kwh = (
        total_reported_cost / annual_load_kwh
        if total_reported_cost is not None and annual_load_kwh
        else None
    )

    return {
        "objective_value": agg.get("objective_optimizing_annual"),
        "lcoe_per_kwh": lcoe_per_kwh,
        "adopted_capacity": _extract_adopted_capacity(model),
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
    """Print a short summary of the solution to the terminal.

    LCOE and adopted capacity print first -- the two headline numbers for a demo/sponsor
    audience -- ahead of the more detailed cost breakdown that follows.
    """
    lcoe = extracted.get("lcoe_per_kwh")
    if lcoe is not None:
        print(f"  LCOE: ${lcoe:,.4f}/kWh")

    adopted = extracted.get("adopted_capacity") or {}
    if adopted:
        print("  Adopted / installed capacity:")
        for tech, entry in adopted.items():
            parts = ", ".join(f"{k}={v:,.2f}" for k, v in entry.items())
            print(f"    {tech}: {parts}")

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

    Columns: ``datetime``, ``load_kwh``, ``import_price_per_kwh``, then one
    ``<block_name>_kwh`` column per top-level block with a nonzero
    ``electricity_source_term`` (e.g. ``solar_pv_kwh``, ``hydrokinetic_kwh``,
    ``diesel_generator_kwh``, ``battery_energy_storage_kwh`` (discharge),
    ``pem_fuel_cell_kwh``, ``utility_kwh`` (grid import)) -- see
    ``timeseries['electricity_source_kwh_by_block']`` in ``extract_solution``. Column set
    varies by case (only blocks that actually generated something appear), which is why
    downstream readers (e.g. ``scripts/plot_weekly_generation.py``) select columns by name
    rather than fixed position.
    """
    path = Path(path)
    ts = extracted.get("timeseries") or {}
    n = len(ts.get("load_kwh") or [])
    if n == 0:
        return
    datetimes = ts.get("datetime") or list(range(n))
    load = (ts.get("load_kwh") or [0.0] * n)[:n]
    price = (ts.get("import_price_per_kwh") or [0.0] * n)
    price = (price + [0.0] * n)[:n]

    by_block = ts.get("electricity_source_kwh_by_block") or {}
    block_names = sorted(by_block.keys())
    block_series = {name: (list(series) + [0.0] * n)[:n] for name, series in by_block.items()}

    header = ["datetime", "load_kwh", "import_price_per_kwh"] + [f"{name}_kwh" for name in block_names]
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(header) + "\n")
        for i in range(n):
            dt = datetimes[i] if i < len(datetimes) else i
            dt_str = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
            row = [dt_str, f"{load[i]:.4f}", f"{price[i]:.6f}"]
            row += [f"{block_series[name][i]:.4f}" for name in block_names]
            f.write(",".join(row) + "\n")
    print(f"  Wrote timeseries to {path}")
