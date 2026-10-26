"""Run the three Igiugig manuscript comparison cases at full 8,760-hour MILP resolution.

Cases (see ``config/cases/igiugig_xlsx.py`` for the underlying baseline parameters --
this script never edits that file or any parameter value in it; it only flips the
module's own documented switches, plus a case-specific subset of ``technology_parameters``
for ``legacy_diesel``):

- ``legacy_diesel``: calibration check against manuscript Table 5. Only
  ``diesel_generator`` and ``battery_energy_storage`` are in play, battery
  ``allow_adoption=False``. Expect LCOE ~0.827, carbon intensity ~0.822.
- ``diesel_on``: full technology set, ``DIESEL_AVAILABLE=True`` (Table 6, $4,000/kW row).
- ``diesel_off``: full technology set, ``DIESEL_AVAILABLE=False`` (Table 7, $4,000/kW row).

Each case solves with Gurobi (MIPGap 0.005, TimeLimit 7200s) and writes
``results/<case>.json`` plus a shared ``results/manifest.json``. Gurobi's own solve log
streams to the console and is also written to ``results/logs/<case>.log``.

Usage (run from the repository root, in an environment with a full -- not size-limited
-- Gurobi license, since an 8,760-period MILP with this technology set has far more than
2,000 variables/constraints)::

    python scripts/run_paper_cases.py                     # all three cases, in order
    python scripts/run_paper_cases.py diesel_on            # just one case
    python scripts/run_paper_cases.py diesel_on diesel_off # a subset, in the order given

Each case can take up to TIME_LIMIT_SECONDS (2 hours) to reach MIP_GAP. A case that
errors or is proven infeasible does not stop the others; it gets a status-only JSON
instead of the full result.
"""

from __future__ import annotations

import copy
import json
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pyomo.environ as pyo  # noqa: E402

import config.cases.igiugig_xlsx as igiugig_case  # noqa: E402
from model.core import build_model  # noqa: E402
from run.build_run_data import build_run_data  # noqa: E402
from shared.financials import annualization_factor_debt_equity  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "results"
LOGS_DIR = RESULTS_DIR / "logs"

MIP_GAP = 0.005
TIME_LIMIT_SECONDS = 7200
EXPECTED_PERIODS = 8760

# Post-solve reporting conversions only (never fed back into the optimization). Values
# match config/cases/igiugig_xlsx.py's own constants / module docstring exactly.
KWH_H2_PER_KG = 33.33
ELECTROLYZER_ELECTRIC_TO_H2_KW = 0.6
DIESEL_ELECTRIC_EFFICIENCY_FOR_CI = 0.33
CO2_KG_PER_KWH_DIESEL_FUEL = 0.2705

CASE_ORDER = ("legacy_diesel", "diesel_on", "diesel_off")

NO_SOLUTION_TERMINATIONS = {
    pyo.TerminationCondition.infeasible,
    pyo.TerminationCondition.infeasibleOrUnbounded,
    pyo.TerminationCondition.unbounded,
}


# ---------------------------------------------------------------------------
# Case configuration: flips config/cases/igiugig_xlsx.py's own module-level
# switches programmatically; never edits the file or re-derives a parameter value.
# ---------------------------------------------------------------------------


def _build_case_config(case_name: str):
    igiugig_case.FORMULATION = "milp"
    igiugig_case.HKT_CAPITAL_COST_PER_KW = 4000.0
    igiugig_case.MAX_HKT_UNITS_PER_SITE = 2
    igiugig_case.EXISTING_DIESEL_UNITS = 3
    igiugig_case.PEAK_LOAD_KW = 109.5
    igiugig_case.PEMFC_ADOPTION_LIMIT_KW = igiugig_case.PEAK_LOAD_KW
    igiugig_case.ELECTROLYZER_ADOPTION_LIMIT_KW = 10.0 * igiugig_case.PEAK_LOAD_KW
    igiugig_case.DIESEL_AVAILABLE = case_name in ("legacy_diesel", "diesel_on")

    case_cfg = igiugig_case.default_igiugig_xlsx_case(PROJECT_ROOT)

    if case_name == "legacy_diesel":
        # Table 5 calibration: only diesel + the existing battery, battery adoption off.
        # Subset (not retype) the module's own resolved dict so no value can drift.
        full_params = case_cfg.technology_parameters
        battery = copy.deepcopy(full_params["battery_energy_storage"])
        battery["allow_adoption"] = False
        diesel = copy.deepcopy(full_params["diesel_generator"])
        case_cfg.technology_parameters = {
            "battery_energy_storage": battery,
            "diesel_generator": diesel,
        }

    return case_cfg


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------


def _solve_case(case_name: str, case_cfg) -> tuple[Any, Any, Any, float]:
    print(f"[{case_name}] Loading data...", flush=True)
    data = build_run_data(PROJECT_ROOT, case_cfg)
    n_periods = len(data.indices["time"])
    if n_periods != EXPECTED_PERIODS:
        raise RuntimeError(
            f"{case_name}: expected {EXPECTED_PERIODS} periods (full hourly year), got {n_periods}"
        )

    technology_parameters = case_cfg.technology_parameters or {}
    financials = asdict(case_cfg.financials) if case_cfg.financials is not None else {}

    print(f"[{case_name}] Building model ({n_periods} periods)...", flush=True)
    model = build_model(data, technology_parameters=technology_parameters, financials=financials)

    solver = pyo.SolverFactory("gurobi")
    if not solver.available():
        raise RuntimeError(
            "Gurobi solver not available in this environment (gurobipy not installed, "
            "or the active license can't be found)."
        )

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{case_name}.log"
    solver.options["MIPGap"] = MIP_GAP
    solver.options["TimeLimit"] = TIME_LIMIT_SECONDS
    solver.options["LogFile"] = str(log_path)

    print(
        f"[{case_name}] Solving with Gurobi (MIPGap={MIP_GAP}, TimeLimit={TIME_LIMIT_SECONDS}s); "
        f"log: {log_path}",
        flush=True,
    )
    t0 = time.time()
    results = solver.solve(model, tee=True)
    wall_seconds = time.time() - t0
    print(
        f"[{case_name}] Solve call returned in {wall_seconds:.1f}s: "
        f"{results.solver.status} / {results.solver.termination_condition}",
        flush=True,
    )

    return model, data, results, wall_seconds


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _expected_series(model, value_fn: Callable[[str, str, int], float]) -> list[float]:
    """Probability-weighted, node-summed per-timestep series (length len(model.T))."""
    T = list(model.T)
    nodes = list(model.NODES)
    scenarios = list(model.SCENARIOS)
    probs = {s: float(pyo.value(model.scenario_probability[s])) for s in scenarios}
    series = [0.0] * len(T)
    for s in scenarios:
        p = probs[s]
        if p == 0.0:
            continue
        for n in nodes:
            for i, t in enumerate(T):
                series[i] += p * value_fn(n, s, t)
    return series


def _monthly_sums(series: list[float], months: list[int]) -> list[float]:
    out = [0.0] * 12
    for v, m in zip(series, months):
        out[m - 1] += v
    return out


def _monthly_means(series: list[float], months: list[int]) -> list[float | None]:
    sums = [0.0] * 12
    counts = [0] * 12
    for v, m in zip(series, months):
        sums[m - 1] += v
        counts[m - 1] += 1
    return [(sums[i] / counts[i]) if counts[i] else None for i in range(12)]


def _hkt_potential_series(model, node: str, profile: str) -> list[float]:
    """Theoretical HKT generation (kWh/timestep) at 100% dispatch: min of the nameplate
    and resource-yield bounds the block itself enforces (see technologies/hydrokinetic/block.py).
    """
    blk = model.hydrokinetic
    T = list(model.T)
    scenarios = list(model.SCENARIOS)
    probs = {s: float(pyo.value(model.scenario_probability[s])) for s in scenarios}
    dt_hours = float(pyo.value(model.time_step_hours))
    cap_kwh = float(pyo.value(blk.total_capacity_kw[node, profile])) * dt_hours
    area = float(pyo.value(blk.total_swept_area_m2[node, profile]))
    out = []
    for t in T:
        val = 0.0
        for s in scenarios:
            resource_kwh = area * float(pyo.value(blk.yield_kwh_per_m2[profile, s, t]))
            val += probs[s] * min(cap_kwh, resource_kwh)
        out.append(val)
    return out


def _solar_potential_series(model, node: str, profile: str) -> list[float]:
    """Theoretical solar generation (kWh/timestep) at 100% dispatch: installed kW x potential."""
    blk = model.solar_pv
    T = list(model.T)
    scenarios = list(model.SCENARIOS)
    probs = {s: float(pyo.value(model.scenario_probability[s])) for s in scenarios}
    cap = float(pyo.value(blk.existing_solar_capacity[node, profile]))
    if hasattr(blk, "solar_capacity_adopted"):
        cap += float(pyo.value(blk.solar_capacity_adopted[node, profile]))
    out = []
    for t in T:
        val = 0.0
        for s in scenarios:
            val += probs[s] * cap * float(pyo.value(blk.solar_potential[node, profile, s, t]))
        out.append(val)
    return out


def _git_commit() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        )
        return commit, dirty
    except Exception:
        return None, None


def _gurobi_version() -> str | None:
    try:
        import gurobipy

        return ".".join(str(x) for x in gurobipy.gurobi.version())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------


def _extract_results(
    case_name: str,
    model,
    data,
    case_cfg,
    solver_results,
    wall_seconds: float,
) -> dict[str, Any]:
    T = list(model.T)
    node = igiugig_case.NODE
    datetimes = data.timeseries.get("datetime") or list(range(len(T)))
    months = [d.month for d in datetimes]

    # --- solver diagnostics ---
    term = solver_results.solver.termination_condition
    status = solver_results.solver.status
    objective = float(pyo.value(model.obj))
    lb = ub = None
    try:
        prob0 = solver_results.problem[0]
        lb = float(prob0.lower_bound)
        ub = float(prob0.upper_bound)
    except Exception:
        pass
    mip_gap_achieved = None
    if lb is not None and ub is not None and abs(ub) > 1e-12:
        mip_gap_achieved = abs(ub - lb) / abs(ub)
    solver_reported_time = getattr(solver_results.solver, "wall_time", None)

    solver_block = {
        "status": str(status),
        "termination_condition": str(term),
        "objective_optimizing_annual": objective,
        "lower_bound": lb,
        "upper_bound": ub,
        "mip_gap_achieved": mip_gap_achieved,
        "mip_gap_target": MIP_GAP,
        "time_limit_seconds": TIME_LIMIT_SECONDS,
        "wall_clock_seconds": wall_seconds,
        "solver_reported_time_seconds": solver_reported_time,
    }

    # --- economics ---
    annual_cost = float(pyo.value(model.total_reported_annual_cost))
    annual_load_kwh = sum(
        _expected_series(model, lambda n, s, t: pyo.value(model.electricity_load[n, s, t]))
    )

    diesel_gen_series = (
        _expected_series(
            model, lambda n, s, t: pyo.value(model.diesel_generator.diesel_generation[n, s, t])
        )
        if hasattr(model, "diesel_generator")
        else [0.0] * len(T)
    )
    diesel_gen_annual = sum(diesel_gen_series)
    carbon_intensity = (
        CO2_KG_PER_KWH_DIESEL_FUEL
        * (diesel_gen_annual / DIESEL_ELECTRIC_EFFICIENCY_FOR_CI)
        / annual_load_kwh
        if annual_load_kwh
        else None
    )

    economics = {
        "annual_cost": annual_cost,
        "annual_load_kwh": annual_load_kwh,
        "lcoe_per_kwh": (annual_cost / annual_load_kwh) if annual_load_kwh else None,
        "carbon_intensity_kg_per_kwh": carbon_intensity,
    }

    # --- generation series (annual + monthly) and curtailment ---
    generation_series_by_source: dict[str, list[float]] = {}
    curtailment_series = [0.0] * len(T)

    if hasattr(model, "hydrokinetic"):
        blk = model.hydrokinetic
        total = [0.0] * len(T)
        for p in blk.HKT:
            actual = _expected_series(
                model, lambda n, s, t, p=p: pyo.value(blk.hkt_generation[n, p, s, t])
            )
            potential = _hkt_potential_series(model, node, p)
            for i in range(len(T)):
                total[i] += actual[i]
                curtailment_series[i] += max(0.0, potential[i] - actual[i])
        generation_series_by_source["hydrokinetic"] = total

    if hasattr(model, "solar_pv"):
        blk = model.solar_pv
        total = [0.0] * len(T)
        for p in blk.SOLAR:
            actual = _expected_series(
                model, lambda n, s, t, p=p: pyo.value(blk.solar_generation[n, p, s, t])
            )
            potential = _solar_potential_series(model, node, p)
            for i in range(len(T)):
                total[i] += actual[i]
                curtailment_series[i] += max(0.0, potential[i] - actual[i])
        generation_series_by_source["solar_pv"] = total

    if hasattr(model, "diesel_generator"):
        generation_series_by_source["diesel_generator"] = diesel_gen_series

    if hasattr(model, "pem_fuel_cell"):
        blk = model.pem_fuel_cell
        generation_series_by_source["pem_fuel_cell"] = _expected_series(
            model, lambda n, s, t: pyo.value(blk.electricity_generation_kwh_electric[n, s, t])
        )

    annual_generation_kwh_by_source = {k: sum(v) for k, v in generation_series_by_source.items()}
    monthly_generation_kwh_by_source = {
        k: _monthly_sums(v, months) for k, v in generation_series_by_source.items()
    }
    total_curtailment_kwh = sum(curtailment_series)

    # --- adopted capacity ---
    adopted_capacity: dict[str, Any] = {}

    if hasattr(model, "hydrokinetic"):
        blk = model.hydrokinetic
        adopted_capacity["hydrokinetic"] = {
            p: {
                "installed_units": int(round(pyo.value(blk.installed_units[node, p]))),
                "installed_kw": float(pyo.value(blk.total_capacity_kw[node, p])),
                "max_units_allowed": int(pyo.value(blk.max_installed_units[node, p])),
            }
            for p in blk.HKT
        }

    if hasattr(model, "solar_pv"):
        blk = model.solar_pv
        by_profile = {}
        for p in blk.SOLAR:
            adopted = (
                float(pyo.value(blk.solar_capacity_adopted[node, p]))
                if hasattr(blk, "solar_capacity_adopted")
                else 0.0
            )
            existing = float(pyo.value(blk.existing_solar_capacity[node, p]))
            by_profile[p] = {
                "adopted_kw": adopted,
                "existing_kw": existing,
                "total_kw": adopted + existing,
            }
        adopted_capacity["solar_pv"] = by_profile

    if hasattr(model, "battery_energy_storage"):
        blk = model.battery_energy_storage
        new_kwh = (
            float(pyo.value(blk.energy_capacity_adopted[node]))
            if hasattr(blk, "energy_capacity_adopted")
            else 0.0
        )
        adopted_capacity["battery_energy_storage"] = {
            "new_kwh": new_kwh,
            "total_kwh": float(pyo.value(blk.total_energy_capacity[node])),
        }

    if hasattr(model, "pem_electrolyzer"):
        blk = model.pem_electrolyzer
        kw_e = (
            float(pyo.value(blk.capacity_adopted_kw[node]))
            if hasattr(blk, "capacity_adopted_kw")
            else 0.0
        )
        adopted_capacity["pem_electrolyzer"] = {
            "adopted_kw_electric": kw_e,
            "adopted_kw_h2": kw_e * ELECTROLYZER_ELECTRIC_TO_H2_KW,
            "adoption_limit_kw_electric": float(pyo.value(blk.capacity_adoption_limit_kw[node])),
        }

    if hasattr(model, "pem_fuel_cell"):
        blk = model.pem_fuel_cell
        kw = (
            float(pyo.value(blk.capacity_adopted_kw[node]))
            if hasattr(blk, "capacity_adopted_kw")
            else 0.0
        )
        adopted_capacity["pem_fuel_cell"] = {
            "adopted_kw": kw,
            "adoption_limit_kw": float(pyo.value(blk.capacity_adoption_limit_kw[node])),
        }

    if hasattr(model, "compressed_gas_hydrogen_storage"):
        blk = model.compressed_gas_hydrogen_storage
        new_kwh_h2 = (
            float(pyo.value(blk.energy_capacity_adopted_kwh_h2_lhv[node]))
            if hasattr(blk, "energy_capacity_adopted_kwh_h2_lhv")
            else 0.0
        )
        adopted_capacity["compressed_gas_hydrogen_storage"] = {
            "new_kwh_h2": new_kwh_h2,
            "new_kg_h2": new_kwh_h2 / KWH_H2_PER_KG,
            "total_kwh_h2": float(pyo.value(blk.total_energy_capacity_kwh_h2_lhv[node])),
        }

    if hasattr(model, "diesel_generator"):
        blk = model.diesel_generator
        installed_units = float(pyo.value(blk.installed_unit_count[node]))
        unit_kw = float(pyo.value(blk.unit_capacity_kw))
        adopted_capacity["diesel_generator"] = {
            "existing_units": installed_units,
            "unit_capacity_kw": unit_kw,
            "installed_capacity_kw": installed_units * unit_kw,
        }

    # --- monthly H2 storage state of charge ---
    monthly_h2_storage = None
    if hasattr(model, "compressed_gas_hydrogen_storage"):
        blk = model.compressed_gas_hydrogen_storage
        inventory_series = _expected_series(
            model, lambda n, s, t: pyo.value(blk.hydrogen_inventory_kwh_h2_lhv[n, s, t])
        )
        total_capacity = float(pyo.value(blk.total_energy_capacity_kwh_h2_lhv[node]))
        monthly_mean_kwh = _monthly_means(inventory_series, months)
        monthly_h2_storage = {
            "monthly_mean_kwh_h2": monthly_mean_kwh,
            "monthly_mean_soc_fraction": [
                (v / total_capacity) if (v is not None and total_capacity > 0) else None
                for v in monthly_mean_kwh
            ],
        }

    # --- resolved parameters actually used (verbatim, to confirm no drift) ---
    financials_dict = asdict(case_cfg.financials) if case_cfg.financials is not None else {}
    resolved_parameters = {
        "technology_parameters": copy.deepcopy(case_cfg.technology_parameters),
        "financials": financials_dict,
        "amortization_factor": annualization_factor_debt_equity(**financials_dict),
        "scenario_switches": {
            "FORMULATION": igiugig_case.FORMULATION,
            "HKT_CAPITAL_COST_PER_KW": igiugig_case.HKT_CAPITAL_COST_PER_KW,
            "MAX_HKT_UNITS_PER_SITE": igiugig_case.MAX_HKT_UNITS_PER_SITE,
            "DIESEL_AVAILABLE": igiugig_case.DIESEL_AVAILABLE,
            "EXISTING_DIESEL_UNITS": igiugig_case.EXISTING_DIESEL_UNITS,
            "PEAK_LOAD_KW": igiugig_case.PEAK_LOAD_KW,
            "PEMFC_ADOPTION_LIMIT_KW": igiugig_case.PEMFC_ADOPTION_LIMIT_KW,
            "ELECTROLYZER_ADOPTION_LIMIT_KW": igiugig_case.ELECTROLYZER_ADOPTION_LIMIT_KW,
        },
    }

    return {
        "case_name": case_name,
        "n_periods": len(T),
        "solver": solver_block,
        "economics": economics,
        "adopted_capacity": adopted_capacity,
        "annual_generation_kwh_by_source": annual_generation_kwh_by_source,
        "total_curtailment_kwh": total_curtailment_kwh,
        "monthly_generation_kwh_by_source": monthly_generation_kwh_by_source,
        "monthly_h2_storage": monthly_h2_storage,
        "resolved_parameters": resolved_parameters,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    case_names = argv or list(CASE_ORDER)
    unknown = [c for c in case_names if c not in CASE_ORDER]
    if unknown:
        print(f"Unknown case(s): {unknown}. Valid: {list(CASE_ORDER)}", file=sys.stderr)
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    run_records: list[str] = []
    for case_name in case_names:
        print(f"\n=== {case_name} ===", flush=True)
        try:
            case_cfg = _build_case_config(case_name)
            model, data, results, wall_seconds = _solve_case(case_name, case_cfg)
            if results.solver.termination_condition in NO_SOLUTION_TERMINATIONS:
                payload: dict[str, Any] = {
                    "case_name": case_name,
                    "solver": {
                        "status": str(results.solver.status),
                        "termination_condition": str(results.solver.termination_condition),
                        "wall_clock_seconds": wall_seconds,
                    },
                    "error": "No solution: solver proved infeasible/unbounded.",
                }
            else:
                payload = _extract_results(case_name, model, data, case_cfg, results, wall_seconds)
        except Exception as exc:  # noqa: BLE001 - one case's failure must not sink the batch
            print(f"[{case_name}] FAILED: {exc}", flush=True)
            traceback.print_exc()
            payload = {"case_name": case_name, "error": str(exc)}

        out_path = RESULTS_DIR / f"{case_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[{case_name}] Wrote {out_path}", flush=True)
        run_records.append(case_name)

    commit, dirty = _git_commit()
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "gurobi_version": _gurobi_version(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cases_run": run_records,
        "mip_gap_target": MIP_GAP,
        "time_limit_seconds": TIME_LIMIT_SECONDS,
    }
    manifest_path = RESULTS_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nWrote {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
