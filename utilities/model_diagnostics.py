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
    by_node = getattr(data, "import_prices_by_node", None)
    if not isinstance(by_node, dict):
        return False
    return any(abs(float(v)) > _FLOAT_TOL for vals in by_node.values() for v in (vals or []))


def _utility_block_present(model: Any) -> bool:
    return getattr(model, "utility", None) is not None


def _nodes(model: Any, data: Any) -> list[str]:
    if getattr(model, "NODES", None) is not None:
        return list(model.NODES)
    return list((getattr(data, "static", {}) or {}).get("electricity_load_keys") or [])


def _import_prices_by_node(model: Any, data: Any) -> dict[str, list[float]]:
    nodes = _nodes(model, data)
    out: dict[str, list[float]] = {}
    by_node = getattr(data, "import_prices_by_node", None)
    if isinstance(by_node, dict):
        for n in nodes:
            vals = by_node.get(n)
            out[n] = [float(v) for v in (vals or [])]
        return out
    return out


def _utility_rates_by_node(model: Any, data: Any) -> dict[str, Any | None]:
    nodes = _nodes(model, data)
    out: dict[str, Any | None] = {}
    by_node = getattr(data, "utility_rate_by_node", None)
    if isinstance(by_node, dict):
        for n in nodes:
            out[n] = by_node.get(n)
        return out
    return out


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


def _rate_from_urdb_structure(struct: Any) -> float:
    """Best-effort extract of ``rate`` from OpenEI/URDB (possibly nested) structures."""
    if struct is None:
        return 0.0
    if isinstance(struct, dict):
        return float(struct.get("rate", 0) or 0.0)
    if isinstance(struct, list) and struct:
        return _rate_from_urdb_structure(struct[0])
    return 0.0


def _extract_applicable_utility_demand_charge_rates_by_node(model: Any, data: Any) -> dict[str, list[float]]:
    """
    Extract demand-charge $/kW rates that correspond to demand-charge *decision variables*
    created in ``model.utility``.

    This avoids needing to re-implement month/tier applicability logic in diagnostics: if the
    model created ``P_flat_y{year}_m{month}`` / ``P_tou_y{year}_m{month}_tier{tier}`` (or legacy
    ``P_flat_m{month}`` / ``P_tou_m{month}_tier{tier}``), those are the applicable rates for this
    run/horizon.
    """
    nodes = _nodes(model, data)
    out: dict[str, list[float]] = {n: [] for n in nodes}
    if not _utility_block_present(model):
        return out

    rates_by_node = _utility_rates_by_node(model, data)
    dts = (getattr(data, "timeseries", {}) or {}).get("datetime") or []
    for n in nodes:
        ur = rates_by_node.get(n)
        dc = getattr(ur, "demand_charges", None) if ur is not None else None
        if not dc:
            continue
        dtype = dc.get("demand_charge_type")
        if dtype in ("flat", "both"):
            applicable = set(dc.get("flat_demand_charge_applicable_months") or [])
            flat_struct = dc.get("flat_demand_charge_structure") or []
            flat_month_map = dc.get("flat_demand_charge_months") or []
            months = sorted({dt.month - 1 for dt in dts if dt is not None})
            for mi in months:
                if applicable and mi not in applicable:
                    continue
                struct_idx = 0
                if mi < len(flat_month_map) and flat_month_map[mi] is not None:
                    try:
                        struct_idx = int(flat_month_map[mi])
                    except (TypeError, ValueError):
                        struct_idx = 0
                if 0 <= struct_idx < len(flat_struct):
                    out[n].append(_rate_from_urdb_structure(flat_struct[struct_idx]))
        if dtype in ("tou", "both"):
            drs = dc.get("demand_charge_ratestructure") or []
            for tier in drs:
                out[n].append(_rate_from_urdb_structure(tier))
    return out


def _check_utility_free_grid_zero_or_missing_costs(model: Any, data: Any) -> list[str]:
    """
    Warn if decision variables exist but the decision-dependent *marginal* utility cost signal
    is effectively zero (energy import prices and demand-charge rates are all ~0).
    """
    if not _utility_block_present(model) or not hasattr(model.utility, "grid_import"):
        return []

    prices_by_node = _import_prices_by_node(model, data)
    rates_by_node = _extract_applicable_utility_demand_charge_rates_by_node(model, data)
    free_nodes: list[str] = []
    for n in _nodes(model, data):
        e_vals = prices_by_node.get(n, [])
        energy_has_positive = any(v > _FLOAT_TOL for v in e_vals)
        d_vals = rates_by_node.get(n, [])
        demand_has_positive = any(v > _FLOAT_TOL for v in d_vals)
        if not energy_has_positive and not demand_has_positive:
            free_nodes.append(n)
    if free_nodes:
        listed = ", ".join(sorted(free_nodes)[:5])
        suffix = "" if len(free_nodes) <= 5 else f" (+{len(free_nodes)-5} more)"
        return [
            "Warning: utility grid-import decision variables exist, but no positive energy or "
            "demand-charge cost was found. Grid imports may be free in the optimization. "
            f"Affected nodes: {listed}{suffix}."
        ]
    return []


def _check_negative_utility_energy_prices(data: Any) -> list[str]:
    # model is not available here, so infer nodes directly from data.
    nodes = list((getattr(data, "static", {}) or {}).get("electricity_load_keys") or [])
    by_node = getattr(data, "import_prices_by_node", None)
    vals_by_node: dict[str, list[float]] = {}
    if isinstance(by_node, dict):
        vals_by_node = {n: [float(v) for v in (by_node.get(n) or [])] for n in nodes}
    else:
        return []
    neg_nodes = [n for n, vals in vals_by_node.items() if any(v < -_FLOAT_TOL for v in vals)]
    if not neg_nodes:
        return []
    neg = [v for vals in vals_by_node.values() for v in vals if v < -_FLOAT_TOL]
    listed = ", ".join(sorted(neg_nodes)[:5])
    suffix = "" if len(neg_nodes) <= 5 else f" (+{len(neg_nodes)-5} more)"
    return [
        "Warning: negative utility energy prices detected "
        f"(min = {min(neg):.6g} $/kWh); grid imports may be rewarded in some periods. "
        f"Affected nodes: {listed}{suffix}."
    ]


def _check_zero_demand_charge_rates(model: Any, data: Any) -> list[str]:
    rates_by_node = _extract_applicable_utility_demand_charge_rates_by_node(model, data)
    zero_nodes = [
        n for n, vals in rates_by_node.items()
        if vals and all(abs(v) <= _FLOAT_TOL for v in vals)
    ]
    if not zero_nodes:
        return []
    listed = ", ".join(sorted(zero_nodes)[:5])
    suffix = "" if len(zero_nodes) <= 5 else f" (+{len(zero_nodes)-5} more)"
    return [
        "Warning: demand-charge variables exist, but all applicable demand-charge rates are zero. "
        f"Peak imports may not be penalized. Affected nodes: {listed}{suffix}."
    ]


def _check_negative_demand_charge_rates(model: Any, data: Any) -> list[str]:
    rates_by_node = _extract_applicable_utility_demand_charge_rates_by_node(model, data)
    neg_nodes: list[str] = []
    neg_rates: list[float] = []
    for n, vals in rates_by_node.items():
        nneg = [v for v in vals if v < -_FLOAT_TOL]
        if nneg:
            neg_nodes.append(n)
            neg_rates.extend(nneg)
    if not neg_nodes:
        return []
    listed = ", ".join(sorted(neg_nodes)[:5])
    suffix = "" if len(neg_nodes) <= 5 else f" (+{len(neg_nodes)-5} more)"
    return [
        "Warning: negative demand-charge rates detected "
        f"(min = {min(neg_rates):.6g} $/kW); peak imports may be rewarded or under-penalized. "
        f"Affected nodes: {listed}{suffix}."
    ]


def _check_negative_import_prices(data: Any) -> list[str]:
    # Backward-compatible alias: earlier tests look for "-0.02" and "negative" substrings.
    return _check_negative_utility_energy_prices(data)


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
    rates_by_node = _utility_rates_by_node(model, data)
    active_nodes = []
    for n, ur in rates_by_node.items():
        dc = getattr(ur, "demand_charges", None) if ur is not None else None
        if _demand_charge_has_nonzero_rates(dc):
            active_nodes.append(n)
    if not active_nodes:
        return []
    static = getattr(data, "static", None) or {}
    dt = static.get("time_step_hours")
    try:
        h = float(dt) if dt is not None else 1.0
    except (TypeError, ValueError):
        h = 1.0
    if abs(h - 1.0) <= 1e-6:
        return []
    listed = ", ".join(sorted(active_nodes)[:5])
    suffix = "" if len(active_nodes) <= 5 else f" (+{len(active_nodes)-5} more)"
    return [
        f"Demand charges are active but time_step_hours is {h:g} (not 1.0): demand-charge treatment "
        "may be incorrect for subhourly runs because import decision variables are in energy units (kWh per period). "
        f"Affected nodes: {listed}{suffix}."
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
    warnings.extend(_check_negative_demand_charge_rates(model, data))
    warnings.extend(_check_zero_demand_charge_rates(model, data))
    warnings.extend(_check_negative_import_prices(data))  # energy-side
    warnings.extend(_check_utility_free_grid_zero_or_missing_costs(model, data))
    warnings.extend(_collect_technology_diagnostics(model, data, case_cfg))
    warnings.extend(_check_demand_subhourly(model, data))
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
