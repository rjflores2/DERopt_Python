"""Primary execution entry point for local case runs."""

import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Ensure project root is on path when run as script (e.g. python run/playground.py or IDE Run)
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pyomo.environ as pyo

from config import get_case_config
from model.core import build_model
from run.build_run_data import build_run_data
from utilities.model_diagnostics import collect_model_diagnostics, print_model_diagnostics
from utilities.results import extract_solution, print_solution_summary, write_timeseries_csv


def main() -> int:
    """Run a first end-to-end data loading path for the default case.

    After loading, prints ``electricity_load_keys`` and ``solar_production_keys`` for
    config authoring (e.g. area limits). Set DEROPT_QUIET=1 to suppress that block.
    """
    t0 = time.perf_counter()
    project_root = Path(__file__).resolve().parents[1]
    case_name = os.getenv("DEROPT_CASE", "Igiugig_xlsx")
    case_cfg = get_case_config(project_root, case_name)

    print("Loading data...")
    t_load = time.perf_counter()
    data = build_run_data(project_root, case_cfg)
    print(f"  Data loading done in {time.perf_counter() - t_load:.1f}s")

    if not os.environ.get("DEROPT_QUIET"):
        load_keys = data.static.get("electricity_load_keys") or []
        print(f"  electricity_load_keys ({len(load_keys)}): {load_keys}")
        solar_keys = data.static.get("solar_production_keys") or []
        if solar_keys:
            print(f"  solar_production_keys ({len(solar_keys)}): {solar_keys}")
        else:
            print("  solar_production_keys: (none)")

    technology_parameters = case_cfg.technology_parameters or {}
    financials = asdict(case_cfg.financials) if case_cfg.financials is not None else {}

    # Optional: write timeseries to CSV for debugging (DEROPT_DEBUG_CSV=1)
    if os.environ.get("DEROPT_DEBUG_CSV"):
        import pandas as pd
        _datetimes = data.timeseries.get("datetime")
        _ts = {
            k: v for k, v in data.timeseries.items()
            if k != "datetime" and isinstance(v, list) and len(v) == len(data.indices["time"])
        }
        df = pd.DataFrame(_ts)
        if _datetimes is not None:
            df.index = _datetimes
        _csv_path = project_root / "temp_debug_data.csv"
        df.to_csv(_csv_path)
        print(f"Debug: wrote {_csv_path}")

    # build_model reads import_prices and utility_rate from data only (single source of truth; no extra args).
    print("Building model...")
    t_model = time.perf_counter()
    model = build_model(data, technology_parameters=technology_parameters, financials=financials)
    print(f"  Model build done in {time.perf_counter() - t_model:.1f}s")
    if model is None:
        raise RuntimeError("build_model returned None; check data has electricity_load_keys, time, time_serial")

    diag = collect_model_diagnostics(model, data, case_cfg)
    if diag:
        print_model_diagnostics(diag)

    # Solve with Gurobi
    solver = pyo.SolverFactory("gurobi")
    if solver.available():
        print("Solving with Gurobi...")
        t_solve = time.perf_counter()
        results = solver.solve(model, tee=bool(os.environ.get("DEROPT_SOLVER_TEE")))
        print(f"  Solve done in {time.perf_counter() - t_solve:.1f}s")
        status = results.solver.status
        term = results.solver.termination_condition
        print(f"Solver: {status} / {term}")
        if status == pyo.SolverStatus.ok and term == pyo.TerminationCondition.optimal:
            extracted = extract_solution(model, data)
            print("Results:")
            print_solution_summary(extracted)
            csv_path = os.environ.get("DEROPT_RESULTS_CSV")
            if os.environ.get("DEROPT_EXPORT_CSV") and not csv_path:
                csv_path = str(project_root / "results_timeseries.csv")
            if csv_path:
                write_timeseries_csv(extracted, csv_path)
    else:
        print("Solver: Gurobi not available (install gurobipy); skipping solve")

    # Summary
    time_count = len(data.indices["time"])
    first_load_key = data.static["electricity_load_keys"][0]
    print(f"Case: {case_cfg.case_name}")
    print(f"Rows loaded: {time_count}")
    print(f"First electricity load (kWh): {data.timeseries[first_load_key][0]:.5f} (key: {first_load_key})")
    print(f"Solar loaded: {'solar_production_keys' in data.static}")
    ur = data.utility_rate
    print(f"Utility rate loaded: {ur is not None}" + (f" ({ur.rate_type}, {ur.name[:40]}...)" if ur else ""))
    ip = data.import_prices
    print(f"Import price vector: {'yes' if ip else 'no'}" + (f" (len={len(ip)})" if ip else ""))
    print(f"Model built: True (solar_pv block: {hasattr(model, 'solar_pv')})")
    print(f"Total elapsed: {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
