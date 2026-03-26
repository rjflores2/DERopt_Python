"""Pre-solve diagnostics: detect suspicious model/data setups (warnings only).

System-wide checks (horizon, utility/tariff, import prices, demand vs timestep) live here.
Equipment capital / O&M warnings are delegated to technology modules that define
``collect_equipment_cost_diagnostics`` (discovered under the ``technologies`` package via
``pkgutil``, excluding ``equipment_cost_diagnostics``).

Also extend via ``register_diagnostic_check`` for ad-hoc hooks. Do not mutate the model or solve.
"""

from __future__ import annotations

from typing import Any, Callable

# Full calendar year in hours (typical hourly 8760 run).
_FULL_YEAR_HOURS = 8760.0
_FLOAT_TOL = 1e-9

# Optional hooks for additional checks (same contract as internal check helpers).
_extra_checks: list[Callable[[Any, Any, Any], list[str]]] = []


def register_diagnostic_check(
    fn: Callable[[Any, Any, Any], list[str]],
) -> Callable[[Any, Any, Any], list[str]]:
    """Register an extra diagnostic function ``(model, data, case_cfg) -> list[str]``."""
    _extra_checks.append(fn)
    return fn


def _hours_represented(data: Any) -> float | None:
    static = getattr(data, "static", None) or {}
    n = len(getattr(data, "indices", {}).get("time", []) or [])
    if n <= 0:
        return None
    dt_h = static.get("time_step_hours")
    try:
        h = float(dt_h) if dt_h is not None else 1.0
    except (TypeError, ValueError):
        h = 1.0
    return float(n) * h


def _demand_charge_has_nonzero_rates(demand_charges: dict[str, Any] | None) -> bool:
    """True if ParsedRate demand_charges has a non-zero $/kW-style rate in flat or TOU structures."""
    if not demand_charges:
        return False
    dtype = demand_charges.get("demand_charge_type")
    if not dtype:
        return False
    if dtype in ("flat", "both"):
        fs = demand_charges.get("flat_demand_charge_structure") or []
        if fs and fs[0]:
            tier0 = fs[0][0] if isinstance(fs[0], list) else fs[0]
            if isinstance(tier0, dict) and abs(float(tier0.get("rate", 0) or 0)) > _FLOAT_TOL:
                return True
    if dtype in ("tou", "both"):
        for tier in demand_charges.get("demand_charge_ratestructure") or []:
            rate = 0.0
            if isinstance(tier, list) and tier:
                rate = float(tier[0].get("rate", 0) if isinstance(tier[0], dict) else 0)
            elif isinstance(tier, dict):
                rate = float(tier.get("rate", 0) or 0)
            if abs(rate) > _FLOAT_TOL:
                return True
    return False


def _has_nonzero_marginal_energy_prices(data: Any) -> bool:
    ip = getattr(data, "import_prices", None)
    if not ip:
        return False
    return any(abs(float(p)) > _FLOAT_TOL for p in ip)


def _utility_block_present(model: Any) -> bool:
    return getattr(model, "utility", None) is not None


def _check_horizon(data: Any) -> list[str]:
    out: list[str] = []
    static = getattr(data, "static", None) or {}
    if static.get("time_subset_applied") is not None:
        out.append(
            "static['time_subset_applied'] is set; this may indicate a debug or reduced-horizon case."
        )
    hrs = _hours_represented(data)
    if hrs is not None and hrs + 1e-6 < _FULL_YEAR_HOURS:
        out.append(
            f"Represented horizon is ~{hrs:.0f} h (< {_FULL_YEAR_HOURS:.0f} h full year); "
            "this may indicate a debug or reduced-horizon case."
        )
    return out


def _check_free_grid(model: Any, data: Any) -> list[str]:
    if not _utility_block_present(model):
        return []
    if _has_nonzero_marginal_energy_prices(data):
        return []
    ur = getattr(data, "utility_rate", None)
    dc = getattr(ur, "demand_charges", None) if ur is not None else None
    if _demand_charge_has_nonzero_rates(dc):
        return []
    return [
        "Utility block is present but energy import prices are all zero (or missing) and there are "
        "no non-zero demand-charge rates: grid energy may be effectively free after any fixed fees. "
        "This may be intentional for debugging, or it may indicate missing tariff inputs."
    ]


def _check_fixed_charge_only_utility(model: Any, data: Any) -> list[str]:
    if not _utility_block_present(model):
        return []
    ur = getattr(data, "utility_rate", None)
    if ur is None:
        return []
    fc = getattr(ur, "customer_fixed_charges", None)
    if not fc:
        return []
    if _has_nonzero_marginal_energy_prices(data):
        return []
    dc = getattr(ur, "demand_charges", None)
    if _demand_charge_has_nonzero_rates(dc):
        return []
    return [
        "Utility has customer_fixed_charges but no energy prices and no demand charges: "
        "utility cost in the objective is constant and grid imports have zero marginal cost."
    ]


def _check_negative_import_prices(data: Any) -> list[str]:
    ip = getattr(data, "import_prices", None)
    if not ip:
        return []
    vals = [float(p) for p in ip]
    neg = [p for p in vals if p < -_FLOAT_TOL]
    if not neg:
        return []
    return [f"import_prices contains negative values (min = {min(neg):.6g} $/kWh)."]


def _iter_technology_diagnostic_collectors():
    """Yield ``collect_equipment_cost_diagnostics`` functions from ``technologies.<module>``."""
    import importlib
    import pkgutil

    import technologies as tech_pkg

    skip = frozenset({"equipment_cost_diagnostics"})
    for info in pkgutil.iter_modules(tech_pkg.__path__):
        if info.name in skip:
            continue
        mod = importlib.import_module(f"technologies.{info.name}")
        fn = getattr(mod, "collect_equipment_cost_diagnostics", None)
        if callable(fn):
            yield fn


def _collect_technology_diagnostics(model: Any, data: Any, case_cfg: Any) -> list[str]:
    """Delegate equipment / O&M warnings to technology modules (plug-in discovery)."""
    out: list[str] = []
    for collect_fn in _iter_technology_diagnostic_collectors():
        out.extend(collect_fn(model, data, case_cfg))
    return out


def _check_demand_subhourly(model: Any, data: Any) -> list[str]:
    ur = getattr(data, "utility_rate", None)
    dc = getattr(ur, "demand_charges", None) if ur is not None else None
    if not _demand_charge_has_nonzero_rates(dc):
        return []
    static = getattr(data, "static", None) or {}
    dt = static.get("time_step_hours")
    try:
        h = float(dt) if dt is not None else 1.0
    except (TypeError, ValueError):
        h = 1.0
    if abs(h - 1.0) <= 1e-6:
        return []
    return [
        f"Demand charges are active but time_step_hours is {h:g} (not 1.0): demand-charge treatment "
        "may be incorrect for subhourly runs because import decision variables are in energy units (kWh per period)."
    ]


def collect_model_diagnostics(
    model: Any,
    data: Any,
    case_cfg: Any = None,
) -> list[str]:
    """Inspect built model and data; return human-readable warning strings (no side effects on the model)."""
    warnings: list[str] = []
    # Order matches documented check categories (horizon, grid/tariff, assets, demand coupling).
    warnings.extend(_check_horizon(data))
    warnings.extend(_check_free_grid(model, data))
    warnings.extend(_check_negative_import_prices(data))
    warnings.extend(_collect_technology_diagnostics(model, data, case_cfg))
    warnings.extend(_check_demand_subhourly(model, data))
    warnings.extend(_check_fixed_charge_only_utility(model, data))
    for fn in _extra_checks:
        warnings.extend(fn(model, data, case_cfg))
    return warnings


def print_model_diagnostics(warnings: list[str]) -> None:
    """Print a concise ``Model diagnostics`` section, one bullet per warning."""
    if not warnings:
        return
    print("Model diagnostics:")
    for w in warnings:
        print(f"- {w}")
