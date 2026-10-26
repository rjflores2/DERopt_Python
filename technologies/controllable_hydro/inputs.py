"""Controllable hydro defaults, validation, and parameter resolution.

Covers both conventional dam hydro and pumped storage hydro in a single formulation
(see ``requirements/controllable_hydro_spec.md``). A unit with zero pump capacity is a
conventional dam; there is no separate module or formulation switch for that case.

All stored energy, inflow, and spill are in kWh of stored energy potential (not volume);
turbine generation and pump consumption are in kWh of electricity at the bus. Capacities
are in kW. Volumetric-to-energy conversion happens in the data loader
(``data_loading/loaders/controllable_hydro.py``), never in this module.

``boundary_condition`` has no default: the choice between ``cyclic``, ``fixed_endpoints``,
and ``free_end`` materially changes results for seasonal storage, so a silent default would
risk a wrong guess. Likewise ``turbine_efficiency``, ``reservoir_capacity_kwh_by_node_unit``,
and ``turbine_capacity_kw_by_node_unit`` have no default and raise if omitted entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BOUNDARY_CYCLIC = "cyclic"
BOUNDARY_FIXED_ENDPOINTS = "fixed_endpoints"
BOUNDARY_FREE_END = "free_end"

_VALID_BOUNDARY_CONDITIONS = frozenset(
    {BOUNDARY_CYCLIC, BOUNDARY_FIXED_ENDPOINTS, BOUNDARY_FREE_END}
)

# Keys with a real default. boundary_condition, turbine_efficiency,
# reservoir_capacity_kwh_by_node_unit, and turbine_capacity_kw_by_node_unit are
# deliberately absent here -- see module docstring.
DEFAULT_CONTROLLABLE_HYDRO_PARAMS: dict[str, Any] = {
    "allow_adoption": False,
    "pump_efficiency": 0.0,
    "inventory_retention": 1.0,
    "minimum_soc_fraction": 0.0,
    "maximum_soc_fraction": 1.0,
    "start_soc_fraction": None,
    "end_soc_fraction": None,
    "pump_capacity_kw_by_node_unit": 0.0,
    "minimum_release_kwh_by_unit_time": None,
    "variable_om_per_kwh": 0.0,
    "fixed_om_per_kw_year": 0.0,
}


@dataclass
class ResolvedControllableHydroInputs:
    """All parameter-derived inputs for the controllable_hydro block (no time series)."""

    boundary_condition: str
    turbine_efficiency: float
    pump_efficiency: float
    inventory_retention: float
    minimum_soc_fraction: float
    maximum_soc_fraction: float
    start_soc_fraction: float | None
    end_soc_fraction: float | None
    reservoir_capacity_kwh: dict[tuple[str, str], float]
    turbine_capacity_kw: dict[tuple[str, str], float]
    pump_capacity_kw: dict[tuple[str, str], float]
    minimum_release_kwh: dict[tuple[str, int], float]
    variable_om_per_kwh: dict[str, float]
    fixed_om_per_kw_year: dict[str, float]


def _dense_capacity_dict(
    nodes: list[str],
    units: list[str],
    raw: Any,
    default: float,
    label: str,
) -> dict[tuple[str, str], float]:
    """Resolve a per-(node, unit) capacity dict.

    ``raw`` may be ``None`` (every pair uses ``default``), a blanket scalar (every pair
    uses that value), or a dict -- either nested ``{node: {unit: value}}`` or flat
    ``{(node, unit): value}`` -- where pairs absent from the dict fall back to ``default``.
    A missing (node, unit) pair is not an error: per the spec, a unit is understood to be
    located at the node(s) where its capacity is non-zero, and zero-capacity pairs are
    permitted and inert.
    """
    if raw is None:
        raw_dict: dict[Any, Any] = {}
        fill = default
    elif isinstance(raw, dict):
        raw_dict = raw
        fill = default
    else:
        raw_dict = {}
        fill = float(raw)

    out: dict[tuple[str, str], float] = {}
    for node in nodes:
        for unit in units:
            v = fill
            nested = raw_dict.get(node)
            if isinstance(nested, dict):
                if unit in nested:
                    v = float(nested[unit])
            elif (node, unit) in raw_dict:
                v = float(raw_dict[(node, unit)])
            if v < 0:
                raise ValueError(
                    f"controllable_hydro: {label} for (node={node!r}, unit={unit!r}) "
                    f"must be >= 0, got {v}"
                )
            out[(node, unit)] = v
    return out


def _per_unit_dict(units: list[str], raw: Any, default: float, label: str) -> dict[str, float]:
    """Resolve a per-unit cost dict; ``raw`` may be ``None``, a blanket scalar, or a dict."""
    if raw is None:
        return {u: default for u in units}
    if isinstance(raw, dict):
        out: dict[str, float] = {}
        for u in units:
            v = float(raw.get(u, default))
            if v < 0:
                raise ValueError(f"controllable_hydro: {label} for unit {u!r} must be >= 0, got {v}")
            out[u] = v
        return out
    v = float(raw)
    if v < 0:
        raise ValueError(f"controllable_hydro: {label} must be >= 0, got {v}")
    return {u: v for u in units}


def _minimum_release_dict(
    units: list[str],
    time_indices: list[int],
    raw: Any,
) -> dict[tuple[str, int], float]:
    """Sparse (unit, t) -> minimum release (kWh). Zero/unset pairs are omitted entirely
    (no trivially-satisfied constraint rows), per spec section 5."""
    out: dict[tuple[str, int], float] = {}
    if not raw or not isinstance(raw, dict):
        return out
    for unit in units:
        nested = raw.get(unit)
        for t in time_indices:
            v = None
            if isinstance(nested, dict) and t in nested:
                v = nested[t]
            elif (unit, t) in raw:
                v = raw[(unit, t)]
            if v is None:
                continue
            v = float(v)
            if v < 0:
                raise ValueError(
                    f"controllable_hydro: minimum_release_kwh_by_unit_time for "
                    f"(unit={unit!r}, t={t!r}) must be >= 0, got {v}"
                )
            if v > 0:
                out[(unit, t)] = v
    return out


def resolve_controllable_hydro_block_inputs(
    controllable_hydro_params: dict[str, Any] | None,
    *,
    nodes: list[str],
    units: list[str],
    time_indices: list[int],
) -> ResolvedControllableHydroInputs:
    """Merge defaults with user overrides and resolve controllable_hydro parameters."""
    user_params = controllable_hydro_params or {}
    params = user_params.copy()
    for key, value in DEFAULT_CONTROLLABLE_HYDRO_PARAMS.items():
        params.setdefault(key, value)

    if bool(params.get("allow_adoption", False)):
        raise ValueError(
            "controllable_hydro: allow_adoption must remain False (hydro capacity is "
            "existing-only in this module; present only for interface symmetry)."
        )

    boundary_condition = params.get("boundary_condition")
    if boundary_condition is None:
        raise ValueError(
            "controllable_hydro: boundary_condition is required (one of "
            f"{sorted(_VALID_BOUNDARY_CONDITIONS)}); no default is provided because the "
            "choice materially changes results for seasonal storage."
        )
    boundary_condition = str(boundary_condition)
    if boundary_condition not in _VALID_BOUNDARY_CONDITIONS:
        raise ValueError(
            f"controllable_hydro: boundary_condition must be one of "
            f"{sorted(_VALID_BOUNDARY_CONDITIONS)}, got {boundary_condition!r}"
        )

    turbine_efficiency = params.get("turbine_efficiency")
    if turbine_efficiency is None:
        raise ValueError("controllable_hydro: turbine_efficiency is required (no default).")
    turbine_efficiency = float(turbine_efficiency)
    if not (0 < turbine_efficiency <= 1):
        raise ValueError(
            f"controllable_hydro: turbine_efficiency must be in (0, 1], got {turbine_efficiency}"
        )

    pump_efficiency = float(params["pump_efficiency"])
    if not (0 <= pump_efficiency <= 1):
        raise ValueError(
            f"controllable_hydro: pump_efficiency must be in [0, 1], got {pump_efficiency}"
        )

    inventory_retention = float(params["inventory_retention"])
    if not (0 < inventory_retention <= 1):
        raise ValueError(
            f"controllable_hydro: inventory_retention must be in (0, 1], got {inventory_retention}"
        )

    minimum_soc_fraction = float(params["minimum_soc_fraction"])
    maximum_soc_fraction = float(params["maximum_soc_fraction"])
    if not (0 <= minimum_soc_fraction <= 1) or not (0 <= maximum_soc_fraction <= 1):
        raise ValueError(
            "controllable_hydro: minimum_soc_fraction and maximum_soc_fraction must each be in [0, 1]."
        )
    if minimum_soc_fraction > maximum_soc_fraction:
        raise ValueError(
            "controllable_hydro: minimum_soc_fraction must be <= maximum_soc_fraction."
        )

    start_soc_fraction = params.get("start_soc_fraction")
    if start_soc_fraction is not None:
        start_soc_fraction = float(start_soc_fraction)
        if not (minimum_soc_fraction <= start_soc_fraction <= maximum_soc_fraction):
            raise ValueError(
                "controllable_hydro: start_soc_fraction must lie within "
                f"[minimum_soc_fraction={minimum_soc_fraction}, maximum_soc_fraction={maximum_soc_fraction}]; "
                f"got {start_soc_fraction}. An out-of-window endpoint would produce an infeasible "
                "model with no useful diagnostic if left unchecked here."
            )

    end_soc_fraction = params.get("end_soc_fraction")
    if end_soc_fraction is not None:
        end_soc_fraction = float(end_soc_fraction)
        if not (minimum_soc_fraction <= end_soc_fraction <= maximum_soc_fraction):
            raise ValueError(
                "controllable_hydro: end_soc_fraction must lie within "
                f"[minimum_soc_fraction={minimum_soc_fraction}, maximum_soc_fraction={maximum_soc_fraction}]; "
                f"got {end_soc_fraction}. An out-of-window endpoint would produce an infeasible "
                "model with no useful diagnostic if left unchecked here."
            )

    if boundary_condition == BOUNDARY_FIXED_ENDPOINTS:
        if start_soc_fraction is None or end_soc_fraction is None:
            raise ValueError(
                "controllable_hydro: boundary_condition='fixed_endpoints' requires both "
                "start_soc_fraction and end_soc_fraction."
            )
    elif boundary_condition == BOUNDARY_FREE_END:
        if start_soc_fraction is None:
            raise ValueError(
                "controllable_hydro: boundary_condition='free_end' requires start_soc_fraction."
            )

    reservoir_capacity_raw = params.get("reservoir_capacity_kwh_by_node_unit")
    if reservoir_capacity_raw is None:
        raise ValueError(
            "controllable_hydro: reservoir_capacity_kwh_by_node_unit is required (no default)."
        )
    reservoir_capacity_kwh = _dense_capacity_dict(
        nodes, units, reservoir_capacity_raw, 0.0, "reservoir_capacity_kwh_by_node_unit"
    )

    turbine_capacity_raw = params.get("turbine_capacity_kw_by_node_unit")
    if turbine_capacity_raw is None:
        raise ValueError(
            "controllable_hydro: turbine_capacity_kw_by_node_unit is required (no default)."
        )
    turbine_capacity_kw = _dense_capacity_dict(
        nodes, units, turbine_capacity_raw, 0.0, "turbine_capacity_kw_by_node_unit"
    )

    pump_capacity_kw = _dense_capacity_dict(
        nodes, units, params.get("pump_capacity_kw_by_node_unit"), 0.0, "pump_capacity_kw_by_node_unit"
    )

    minimum_release_kwh = _minimum_release_dict(
        units, time_indices, params.get("minimum_release_kwh_by_unit_time")
    )

    variable_om_per_kwh = _per_unit_dict(
        units, params.get("variable_om_per_kwh"), 0.0, "variable_om_per_kwh"
    )
    fixed_om_per_kw_year = _per_unit_dict(
        units, params.get("fixed_om_per_kw_year"), 0.0, "fixed_om_per_kw_year"
    )

    return ResolvedControllableHydroInputs(
        boundary_condition=boundary_condition,
        turbine_efficiency=turbine_efficiency,
        pump_efficiency=pump_efficiency,
        inventory_retention=inventory_retention,
        minimum_soc_fraction=minimum_soc_fraction,
        maximum_soc_fraction=maximum_soc_fraction,
        start_soc_fraction=start_soc_fraction,
        end_soc_fraction=end_soc_fraction,
        reservoir_capacity_kwh=reservoir_capacity_kwh,
        turbine_capacity_kw=turbine_capacity_kw,
        pump_capacity_kw=pump_capacity_kw,
        minimum_release_kwh=minimum_release_kwh,
        variable_om_per_kwh=variable_om_per_kwh,
        fixed_om_per_kw_year=fixed_om_per_kw_year,
    )
