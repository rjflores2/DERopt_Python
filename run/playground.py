"""Primary execution entry point for local case runs."""

import os
import sys
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


def main() -> int:
    """Run a first end-to-end data loading path for the default case."""
    project_root = Path(__file__).resolve().parents[1]
    case_name = os.getenv("DEROPT_CASE", "Igiugig_xlsx")
    case_cfg = get_case_config(project_root, case_name)

    data = build_run_data(project_root, case_cfg)

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
    model = build_model(data, technology_parameters=technology_parameters, financials=financials)
    if model is None:
        raise RuntimeError("build_model returned None; check data has electricity_load_keys, time, time_serial")

    # Solve with Gurobi
    solver = pyo.SolverFactory("gurobi")
    if solver.available():
        results = solver.solve(model, tee=bool(os.environ.get("DEROPT_SOLVER_TEE")))
        status = results.solver.status
        term = results.solver.termination_condition
        obj_val = pyo.value(model.obj) if model.obj.is_constructed() else None
        print(f"Solver: {status} / {term}")
        if obj_val is not None:
            print(f"Objective value: {obj_val:.2f}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
