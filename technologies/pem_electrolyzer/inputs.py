"""PEM electrolyzer technology inputs: defaults, validation, and resolution.

All hydrogen quantities in this technology are expressed on a **lower heating value
(LHV)** basis in **kWh-H2_LHV** (chemical energy of hydrogen per timestep). This is
the canonical hydrogen energy unit for the DERopt model; **higher heating value
(HHV) is not used** internally.

Electrical energy is in **kWh-electric per timestep** (same basis as site electricity
loads and other DER flows).

The LHV conversion efficiency ``electric_to_hydrogen_lhv_efficiency`` is the ratio
**(kWh-H2_LHV produced) / (kWh-electric consumed)** per timestep.

``formulation`` must be an exact string (``pem_electrolyzer_lp``, ``pem_electrolyzer_binary``,
``pem_electrolyzer_unit_milp``), matching the diesel pattern ``<technology>_<model>``; no aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


# Formulation strings follow the diesel pattern: ``<technology>_<model>`` (exact strings; no aliases).
FORMULATION_PEM_ELECTROLYZER_LP = "pem_electrolyzer_lp"
FORMULATION_PEM_ELECTROLYZER_BINARY = "pem_electrolyzer_binary"
FORMULATION_PEM_ELECTROLYZER_UNIT_MILP = "pem_electrolyzer_unit_milp"

_VALID_FORMULATIONS = frozenset(
    {
        FORMULATION_PEM_ELECTROLYZER_LP,
        FORMULATION_PEM_ELECTROLYZER_BINARY,
        FORMULATION_PEM_ELECTROLYZER_UNIT_MILP,
    }
)


DEFAULT_PEM_ELECTROLYZER_PARAMS = {
    "allow_adoption": True,
    "formulation": FORMULATION_PEM_ELECTROLYZER_LP,
    "capital_cost_per_kw": 1200.0,
    "capital_cost_per_unit": 1_200_000.0,
    "fixed_om_per_kw_year": 25.0,
    "fixed_om_per_unit_year": 25_000.0,
    "variable_om_per_kwh_electric": 0.0,
    "electric_to_hydrogen_lhv_efficiency": 0.65,
    "minimum_loading_fraction": 0.2,
    "electrolyzer_binary_big_m_load_multiplier": 15.0,
    "unit_capacity_kw": 1000.0,
    "existing_capacity_kw_by_node": None,
    "existing_unit_count_by_node": None,
    "capacity_adoption_limit_kw_by_node": None,
    "unit_adoption_limit_by_node": None,
}


@dataclass(frozen=True)
class PemElectrolyzerBlockInputs:
    """Resolved inputs for building the PEM electrolyzer Pyomo block."""

    allow_adoption: bool
    formulation: str
    time_step_hours: float
    capital_cost_per_kw: float
    capital_cost_per_unit: float
    fixed_om_per_kw_year: float
    fixed_om_per_unit_year: float
    variable_om_per_kwh_electric: float
    electric_to_hydrogen_lhv_efficiency: float
    minimum_loading_fraction: float
    electrolyzer_binary_big_m_load_multiplier: float
    existing_capacity_kw_by_node: dict[str, float]
    existing_unit_count_by_node: dict[str, int]
    unit_capacity_kw: float
    capacity_adoption_limit_kw_by_node: dict[str, float]
    unit_adoption_limit_by_node: dict[str, int]
    amortization_factor: float


def _nonnegative_float_by_node(raw: dict[str, Any] | None, *, nodes: list[str], label: str) -> dict[str, float]:
    out: dict[str, float] = {}
    src = raw or {}
    for node in nodes:
        value = float(src.get(node, 0.0))
        if value < 0:
            raise ValueError(f"pem_electrolyzer: {label} for node {node!r} must be >= 0, got {value}.")
        out[node] = value
    return out


def _nonnegative_int_by_node(raw: dict[str, Any] | None, *, nodes: list[str], label: str) -> dict[str, int]:
    out: dict[str, int] = {}
    src = raw or {}
    for node in nodes:
        raw_value = src.get(node, 0)
        value = int(raw_value)
        if value < 0:
            raise ValueError(f"pem_electrolyzer: {label} for node {node!r} must be >= 0, got {value}.")
        if float(raw_value) != float(value):
            raise ValueError(f"pem_electrolyzer: {label} for node {node!r} must be an integer, got {raw_value}.")
        out[node] = value
    return out


def resolve_pem_electrolyzer_block_inputs(
    pem_electrolyzer_params: dict[str, Any] | None,
    *,
    time_step_hours: float,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> PemElectrolyzerBlockInputs:
    """Merge defaults with user overrides and resolve per-node parameters."""
    user_params = pem_electrolyzer_params or {}
    params = user_params.copy()
    for key, value in DEFAULT_PEM_ELECTROLYZER_PARAMS.items():
        params.setdefault(key, value)

    allow_adoption = bool(params["allow_adoption"])
    formulation = str(params["formulation"]).strip().lower()
    if formulation not in _VALID_FORMULATIONS:
        raise ValueError(
            "pem_electrolyzer: formulation must be one of "
            f"{sorted(_VALID_FORMULATIONS)}, got {params['formulation']!r}."
        )

    dt = float(time_step_hours)
    if dt <= 0:
        raise ValueError("pem_electrolyzer: time_step_hours must be positive.")

    capital_cost_per_kw = float(params["capital_cost_per_kw"])
    capital_cost_per_unit = float(params["capital_cost_per_unit"])
    fixed_om_per_kw_year = float(params["fixed_om_per_kw_year"])
    fixed_om_per_unit_year = float(params["fixed_om_per_unit_year"])
    variable_om_per_kwh_electric = float(params["variable_om_per_kwh_electric"])
    electric_to_hydrogen_lhv_efficiency = float(params["electric_to_hydrogen_lhv_efficiency"])
    minimum_loading_fraction = float(params["minimum_loading_fraction"])
    electrolyzer_binary_big_m_load_multiplier = float(params["electrolyzer_binary_big_m_load_multiplier"])
    unit_capacity_kw = float(params["unit_capacity_kw"])

    if not (0.0 < electric_to_hydrogen_lhv_efficiency <= 1.0):
        raise ValueError(
            "pem_electrolyzer: electric_to_hydrogen_lhv_efficiency must be in (0, 1] "
            "(kWh-H2_LHV out per kWh-electric in)."
        )
    if not (0.0 <= minimum_loading_fraction <= 1.0):
        raise ValueError("pem_electrolyzer: minimum_loading_fraction must be in [0, 1].")
    if electrolyzer_binary_big_m_load_multiplier <= 0:
        raise ValueError(
            "pem_electrolyzer: electrolyzer_binary_big_m_load_multiplier must be positive."
        )
    if unit_capacity_kw <= 0:
        raise ValueError("pem_electrolyzer: unit_capacity_kw must be > 0.")
    if min(
        capital_cost_per_kw,
        capital_cost_per_unit,
        fixed_om_per_kw_year,
        fixed_om_per_unit_year,
        variable_om_per_kwh_electric,
    ) < 0:
        raise ValueError("pem_electrolyzer: cost inputs must be >= 0.")

    existing_capacity_kw_by_node = _nonnegative_float_by_node(
        params.get("existing_capacity_kw_by_node"),
        nodes=nodes,
        label="existing_capacity_kw_by_node",
    )
    existing_unit_count_by_node = _nonnegative_int_by_node(
        params.get("existing_unit_count_by_node"),
        nodes=nodes,
        label="existing_unit_count_by_node",
    )
    capacity_adoption_limit_kw_by_node = _nonnegative_float_by_node(
        params.get("capacity_adoption_limit_kw_by_node"),
        nodes=nodes,
        label="capacity_adoption_limit_kw_by_node",
    )
    unit_adoption_limit_by_node = _nonnegative_int_by_node(
        params.get("unit_adoption_limit_by_node"),
        nodes=nodes,
        label="unit_adoption_limit_by_node",
    )

    if not allow_adoption:
        capacity_adoption_limit_kw_by_node = {node: 0.0 for node in nodes}
        unit_adoption_limit_by_node = {node: 0 for node in nodes}

    return PemElectrolyzerBlockInputs(
        allow_adoption=allow_adoption,
        formulation=formulation,
        time_step_hours=dt,
        capital_cost_per_kw=capital_cost_per_kw,
        capital_cost_per_unit=capital_cost_per_unit,
        fixed_om_per_kw_year=fixed_om_per_kw_year,
        fixed_om_per_unit_year=fixed_om_per_unit_year,
        variable_om_per_kwh_electric=variable_om_per_kwh_electric,
        electric_to_hydrogen_lhv_efficiency=electric_to_hydrogen_lhv_efficiency,
        minimum_loading_fraction=minimum_loading_fraction,
        electrolyzer_binary_big_m_load_multiplier=electrolyzer_binary_big_m_load_multiplier,
        existing_capacity_kw_by_node=existing_capacity_kw_by_node,
        existing_unit_count_by_node=existing_unit_count_by_node,
        unit_capacity_kw=unit_capacity_kw,
        capacity_adoption_limit_kw_by_node=capacity_adoption_limit_kw_by_node,
        unit_adoption_limit_by_node=unit_adoption_limit_by_node,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
    )
