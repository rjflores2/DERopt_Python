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

# Set False to silence Gurobi's live solver log; True streams it to the console as it solves.
SOLVER_TEE = True

# Bound the MIP search so a live run always finishes in bounded time -- the full Igiugig
# case (diesel unit commitment + integer HKT units + electrolyzer/fuel-cell binaries, full
# 8760-hour year) is a large MILP (~140k variables, ~17.5k binaries); scripts/run_paper_cases.py
# budgets up to 2 hours per case at a 0.5% gap for this same model. Set to None to disable
# either limit (unbounded search, matching pre-demo behavior). Gurobi reports the best
# solution found and its remaining optimality gap either way, which is a legitimate result
# to present even when a limit stops the search short of proven-optimal.
SOLVER_NODE_LIMIT: int | None = 1000
SOLVER_TIME_LIMIT_SECONDS: float | None = 600.0  # safety net; NodeLimit is expected to bind first

# When DEROPT_EXPORT_CSV also produces results_timeseries.csv (see below), automatically
# render the stacked-area weekly generation chart from it too -- one press-play, no separate
# manual plotting step. Set to False to skip chart generation even when CSV export is on.
# Week selection: DEROPT_CHART_START_DATE (e.g. "2022-07-04") takes priority if set; otherwise
# CHART_START_INDEX (default ~July 1 in a non-leap 8760-hour year -- a representative summer
# week with meaningful solar) via DEROPT_CHART_START_INDEX. DEROPT_CHART_HOURS overrides the
# window length (default: 168h = 1 week).
GENERATE_WEEKLY_CHART = True
CHART_START_INDEX = 4344


def main() -> int:
    """Run a first end-to-end data loading path for the default case.

    After loading, prints ``electricity_load_keys`` and ``solar_production_keys`` for
    config authoring (e.g. area limits). Set DEROPT_QUIET=1 to suppress that block.
    """
    t0 = time.perf_counter()
    project_root = Path(__file__).resolve().parents[1]
    case_name = os.getenv("DEROPT_CASE", "stochastic_demo")
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
        if SOLVER_NODE_LIMIT is not None:
            solver.options["NodeLimit"] = SOLVER_NODE_LIMIT
        if SOLVER_TIME_LIMIT_SECONDS is not None:
            solver.options["TimeLimit"] = SOLVER_TIME_LIMIT_SECONDS
        print(
            f"Solving with Gurobi (NodeLimit={SOLVER_NODE_LIMIT}, "
            f"TimeLimit={SOLVER_TIME_LIMIT_SECONDS})..."
        )
        t_solve = time.perf_counter()
        results = solver.solve(model, tee=SOLVER_TEE)
        print(f"  Solve done in {time.perf_counter() - t_solve:.1f}s")
        status = results.solver.status
        term = results.solver.termination_condition
        print(f"Solver: {status} / {term}")
        # A node/time limit stops the search early with the best incumbent found so far --
        # still a usable (feasible, just not provably optimal) result. Pyomo's Gurobi plugin
        # reports status='aborted' (not 'ok') for these, verified directly against
        # GUROBI_RUN.py's GRB.NODE_LIMIT/GRB.TIME_LIMIT handling -- so termination_condition,
        # not solver status, is what actually distinguishes "has a usable solution" here.
        terms_with_a_usable_solution = (
            pyo.TerminationCondition.optimal,
            pyo.TerminationCondition.maxTimeLimit,
            pyo.TerminationCondition.maxEvaluations,  # Pyomo's mapping for Gurobi NodeLimit stops
        )
        if term in terms_with_a_usable_solution and pyo.value(model.obj, exception=False) is not None:
            if term != pyo.TerminationCondition.optimal:
                print(
                    f"  Note: search stopped early ({term}); showing the best solution found, "
                    "not a proven optimum."
                )
            extracted = extract_solution(model, data)
            print("Results:")
            print_solution_summary(extracted)
            csv_path = os.environ.get("DEROPT_RESULTS_CSV")
            if os.environ.get("DEROPT_EXPORT_CSV") and not csv_path:
                csv_path = str(project_root / "results_timeseries.csv")
            if csv_path:
                write_timeseries_csv(extracted, csv_path)
                if GENERATE_WEEKLY_CHART:
                    try:
                        from scripts.plot_weekly_generation import plot_weekly_generation

                        chart_path = plot_weekly_generation(
                            csv_path,
                            project_root / "weekly_generation.png",
                            start_date=os.environ.get("DEROPT_CHART_START_DATE"),
                            start_index=int(os.environ.get("DEROPT_CHART_START_INDEX", CHART_START_INDEX)),
                            hours=int(os.environ.get("DEROPT_CHART_HOURS", 168)),
                        )
                        print(f"  Wrote weekly generation chart to {chart_path}")
                    except Exception as exc:
                        # Chart generation is a nice-to-have on top of the console/CSV
                        # results, which are already printed/written by this point -- don't
                        # let a plotting hiccup (e.g. an out-of-range default week for a
                        # shorter/subset run) take down an otherwise-successful solve.
                        print(f"  Weekly chart generation skipped ({exc})")
    else:
        print("Solver: Gurobi not available (install gurobipy); skipping solve")

    # Summary
    time_count = len(data.indices["time"])
    first_load_key = data.static["electricity_load_keys"][0]
    print(f"Case: {case_cfg.case_name}")
    print(f"Rows loaded: {time_count}")
    print(f"First electricity load (kWh): {data.timeseries[first_load_key][0]:.5f} (key: {first_load_key})")
    print(f"Solar loaded: {'solar_production_keys' in data.static}")
    ur_by_node = getattr(data, "utility_rate_by_node", None) or {}
    has_utility_rate = any(ur is not None for ur in ur_by_node.values())
    print(f"Utility rate loaded (any node): {has_utility_rate}")
    ip_by_node = getattr(data, "import_prices_by_node", None) or {}
    sample_price_len = len(next(iter(ip_by_node.values()))) if ip_by_node else 0
    print(
        f"Import price vectors by node: {'yes' if ip_by_node else 'no'}"
        + (f" (nodes={len(ip_by_node)}, periods={sample_price_len})" if ip_by_node else "")
    )
    print(f"Model built: True (solar_pv block: {hasattr(model, 'solar_pv')})")
    print(f"Total elapsed: {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
