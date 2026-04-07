"""Diesel generator defaults, validation, and parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


FORMULATION_CONTINUOUS_LP = "continuous_lp"
FORMULATION_COMMITMENT_MILP = "commitment_milp"
FORMULATION_DISCRETE_UNITS_MILP = "discrete_units_milp"
VALID_FORMULATIONS = {
    FORMULATION_CONTINUOUS_LP,
    FORMULATION_COMMITMENT_MILP,
    FORMULATION_DISCRETE_UNITS_MILP,
}

DEFAULT_DIESEL_GENERATOR_PARAMS = {
    "allow_adoption": True,
    "formulation": FORMULATION_CONTINUOUS_LP,
    "capital_cost_per_kw": 900.0,
    "capital_cost_per_unit": 90000.0,
    "fixed_om_per_kw_year": 20.0,
    "fixed_om_per_unit_year": 2000.0,
    "variable_om_per_kwh": 0.02,
    "fuel_cost_per_kwh_fuel": 0.08,
    "electric_efficiency": 0.35,
    "minimum_loading_fraction": 0.0,
    "unit_capacity_kw": 100.0,
    "existing_capacity_by_node": None,
    "existing_unit_count_by_node": None,
    "capacity_adoption_limit_by_node": None,
    "unit_adoption_limit_by_node": None,
}


@dataclass
class ResolvedDieselGeneratorInputs:
    """Resolved diesel parameters for model construction."""

    allow_adoption: bool
    formulation: str
    capital_cost_per_kw: float
    capital_cost_per_unit: float
    fixed_om_per_kw_year: float
    fixed_om_per_unit_year: float
    variable_om_per_kwh: float
    fuel_cost_per_kwh_fuel: float
    electric_efficiency: float
    effective_fuel_cost_per_kwh_electric: float
    minimum_loading_fraction: float
    unit_capacity_kw: float
    existing_capacity_by_node: dict[str, float]
    existing_unit_count_by_node: dict[str, int]
    capacity_adoption_limit_by_node: dict[str, float]
    unit_adoption_limit_by_node: dict[str, int]
    amortization_factor: float


def _resolve_nonnegative_float_by_node(
    raw: dict[str, Any] | None,
    *,
    nodes: list[str],
    label: str,
) -> dict[str, float]:
    out: dict[str, float] = {}
    src = raw or {}
    for node in nodes:
        value = float(src.get(node, 0.0))
        if value < 0:
            raise ValueError(f"diesel_generator: {label} for node {node!r} must be >= 0, got {value}.")
        out[node] = value
    return out


def _resolve_nonnegative_int_by_node(
    raw: dict[str, Any] | None,
    *,
    nodes: list[str],
    label: str,
) -> dict[str, int]:
    out: dict[str, int] = {}
    src = raw or {}
    for node in nodes:
        raw_value = src.get(node, 0)
        value = int(raw_value)
        if value < 0:
            raise ValueError(f"diesel_generator: {label} for node {node!r} must be >= 0, got {value}.")
        if float(raw_value) != float(value):
            raise ValueError(f"diesel_generator: {label} for node {node!r} must be an integer, got {raw_value}.")
        out[node] = value
    return out


def resolve_diesel_generator_block_inputs(
    diesel_generator_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> ResolvedDieselGeneratorInputs:
    """Merge defaults with user overrides and resolve diesel-generator parameters."""
    params = (diesel_generator_params or {}).copy()
    for key, value in DEFAULT_DIESEL_GENERATOR_PARAMS.items():
        params.setdefault(key, value)

    allow_adoption = bool(params["allow_adoption"])
    formulation = str(params["formulation"])
    if formulation not in VALID_FORMULATIONS:
        raise ValueError(
            "diesel_generator: formulation must be one of "
            f"{sorted(VALID_FORMULATIONS)}, got {formulation!r}."
        )

    capital_cost_per_kw = float(params["capital_cost_per_kw"])
    capital_cost_per_unit = float(params["capital_cost_per_unit"])
    fixed_om_per_kw_year = float(params["fixed_om_per_kw_year"])
    fixed_om_per_unit_year = float(params["fixed_om_per_unit_year"])
    variable_om_per_kwh = float(params["variable_om_per_kwh"])
    fuel_cost_per_kwh_fuel = float(params["fuel_cost_per_kwh_fuel"])
    electric_efficiency = float(params["electric_efficiency"])
    minimum_loading_fraction = float(params["minimum_loading_fraction"])
    unit_capacity_kw = float(params["unit_capacity_kw"])

    if electric_efficiency <= 0 or electric_efficiency > 1:
        raise ValueError("diesel_generator: electric_efficiency must be in (0, 1].")
    if unit_capacity_kw <= 0:
        raise ValueError("diesel_generator: unit_capacity_kw must be > 0.")
    if not (0 <= minimum_loading_fraction <= 1):
        raise ValueError("diesel_generator: minimum_loading_fraction must be in [0, 1].")

    if min(
        capital_cost_per_kw,
        capital_cost_per_unit,
        fixed_om_per_kw_year,
        fixed_om_per_unit_year,
        variable_om_per_kwh,
        fuel_cost_per_kwh_fuel,
    ) < 0:
        raise ValueError("diesel_generator: cost inputs must be >= 0.")

    existing_capacity_by_node = _resolve_nonnegative_float_by_node(
        params.get("existing_capacity_by_node"),
        nodes=nodes,
        label="existing_capacity_by_node",
    )
    existing_unit_count_by_node = _resolve_nonnegative_int_by_node(
        params.get("existing_unit_count_by_node"),
        nodes=nodes,
        label="existing_unit_count_by_node",
    )
    capacity_adoption_limit_by_node = _resolve_nonnegative_float_by_node(
        params.get("capacity_adoption_limit_by_node"),
        nodes=nodes,
        label="capacity_adoption_limit_by_node",
    )
    unit_adoption_limit_by_node = _resolve_nonnegative_int_by_node(
        params.get("unit_adoption_limit_by_node"),
        nodes=nodes,
        label="unit_adoption_limit_by_node",
    )

    if not allow_adoption:
        capacity_adoption_limit_by_node = {node: 0.0 for node in nodes}
        unit_adoption_limit_by_node = {node: 0 for node in nodes}

    return ResolvedDieselGeneratorInputs(
        allow_adoption=allow_adoption,
        formulation=formulation,
        capital_cost_per_kw=capital_cost_per_kw,
        capital_cost_per_unit=capital_cost_per_unit,
        fixed_om_per_kw_year=fixed_om_per_kw_year,
        fixed_om_per_unit_year=fixed_om_per_unit_year,
        variable_om_per_kwh=variable_om_per_kwh,
        fuel_cost_per_kwh_fuel=fuel_cost_per_kwh_fuel,
        electric_efficiency=electric_efficiency,
        effective_fuel_cost_per_kwh_electric=fuel_cost_per_kwh_fuel / electric_efficiency,
        minimum_loading_fraction=minimum_loading_fraction,
        unit_capacity_kw=unit_capacity_kw,
        existing_capacity_by_node=existing_capacity_by_node,
        existing_unit_count_by_node=existing_unit_count_by_node,
        capacity_adoption_limit_by_node=capacity_adoption_limit_by_node,
        unit_adoption_limit_by_node=unit_adoption_limit_by_node,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
    )
