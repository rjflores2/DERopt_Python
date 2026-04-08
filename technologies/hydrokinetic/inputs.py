"""Hydrokinetic (run-of-river style) defaults and resolved inputs.

MATLAB reference (DERopt):
- LP: ``opt_run_of_river.m`` — generation <= river_power_potential * swept_area; area cap.
- MILP: ``opt_integer_run_of_river.m`` — elec <= potential * unit_swept_area * units; unit cap.
- Costs: ``opt_var_cf.m`` (integer: debt * v(2,:) * units with v = $/kW, kW/unit, ...).

Resource scaling: timeseries are kWh/kW per period (from loader). Convert to kWh/m²/s per period via
``yield_m2 = potential_kwh_per_kw * (reference_kw / reference_swept_area_m2)`` so
``generation <= swept_area_m2 * yield_m2[t]`` matches the LP formulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity

FORMULATION_HYDROKINETIC_LP = "hydrokinetic_lp"
FORMULATION_HYDROKINETIC_UNIT_MILP = "hydrokinetic_unit_milp"

DEFAULT_HYDROKINETIC_PARAMS: dict[str, Any] = {
    "formulation": FORMULATION_HYDROKINETIC_LP,
    "allow_adoption": True,
    # LP / shared
    "capital_cost_per_kw": 2000.0,
    "capital_cost_per_m2": 0.0,
    "fixed_om_per_kw_year": 0.0,
    "fixed_om_per_m2_year": 0.0,
    "variable_om_per_kwh": 0.0,
    # Couple nameplate kW to swept area (LP): adopted_kW <= density * (existing_area + adopted_area).
    "max_power_density_kw_per_m2": 1.0e6,
    # MILP unit specs (Igiugig-style defaults from tech_select_Igiugig.m when ror_integer_on)
    "unit_swept_area_m2": 18.0,
    "unit_capacity_kw": 80.0,
    "capital_cost_per_unit": None,
    "fixed_om_per_unit_year": 0.0,
}


@dataclass
class ResolvedHydrokineticInputs:
    formulation: str
    allow_adoption: bool
    amortization_factor: float
    time_step_hours: float
    # Per profile index aligned with profiles list
    capital_cost_per_kw: list[float]
    capital_cost_per_m2: list[float]
    fixed_om_per_kw_year: list[float]
    fixed_om_per_m2_year: list[float]
    variable_om_per_kwh: list[float]
    max_power_density_kw_per_m2: list[float]
    unit_swept_area_m2: list[float]
    unit_capacity_kw: list[float]
    annual_capital_per_unit: list[float]
    fixed_om_per_unit_year: list[float]
    max_installed_units_by_node_profile: dict[tuple[str, str], int]
    existing_units_by_node_profile: dict[tuple[str, str], int]
    existing_swept_area_m2: dict[tuple[str, str], float]
    existing_capacity_kw: dict[tuple[str, str], float]
    max_swept_area_m2: dict[tuple[str, str], float]
    yield_kwh_per_m2_init: dict[tuple[str, int], float]


def _profile_overrides(
    profiles: list[str],
    global_params: dict[str, Any],
    profile_key: str,
) -> dict[str, Any]:
    by_profile = global_params.get("params_by_profile")
    if by_profile is None:
        return {}
    if isinstance(by_profile, dict):
        return (by_profile.get(profile_key) or {}).copy()
    return {}


def _merge_params(global_params: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return {**global_params, **overrides}


def _int_dict(
    nodes: list[str],
    profiles: list[str],
    raw: dict[Any, Any] | None,
    default: int,
    label: str,
) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    raw = raw or {}
    for node in nodes:
        for profile in profiles:
            v = default
            if isinstance(raw.get(node), dict):
                if profile in raw[node]:
                    v = int(raw[node][profile])
            elif (node, profile) in raw:
                v = int(raw[(node, profile)])
            if v < 0:
                raise ValueError(
                    f"hydrokinetic: {label} for (node={node!r}, profile={profile!r}) must be >= 0, got {v}"
                )
            out[(node, profile)] = v
    return out


def _float_dict(
    nodes: list[str],
    profiles: list[str],
    raw: dict[Any, Any] | None,
    default: float,
    label: str,
    *,
    positive: bool = False,
) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    raw = raw or {}
    for node in nodes:
        for profile in profiles:
            v = default
            if isinstance(raw.get(node), dict):
                if profile in raw[node]:
                    v = float(raw[node][profile])
            elif (node, profile) in raw:
                v = float(raw[(node, profile)])
            if v < 0:
                raise ValueError(
                    f"hydrokinetic: {label} for (node={node!r}, profile={profile!r}) must be >= 0, got {v}"
                )
            if positive and v <= 0 and default != v:
                raise ValueError(
                    f"hydrokinetic: {label} for (node={node!r}, profile={profile!r}) must be > 0, got {v}"
                )
            out[(node, profile)] = v
    return out


def resolve_hydrokinetic_block_inputs(
    hydrokinetic_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
    profiles: list[str],
    production_by_profile: dict[str, list[float]],
    *,
    reference_kw: float,
    reference_swept_area_m2: float,
    time_indices: list[int],
    time_step_hours: float,
) -> ResolvedHydrokineticInputs:
    params = (hydrokinetic_params or {}).copy()
    for key, value in DEFAULT_HYDROKINETIC_PARAMS.items():
        params.setdefault(key, value)

    formulation = str(params["formulation"])
    if formulation not in (FORMULATION_HYDROKINETIC_LP, FORMULATION_HYDROKINETIC_UNIT_MILP):
        raise ValueError(
            f"hydrokinetic: formulation must be {FORMULATION_HYDROKINETIC_LP!r} or "
            f"{FORMULATION_HYDROKINETIC_UNIT_MILP!r}, got {formulation!r}"
        )

    allow_adoption = bool(params["allow_adoption"])
    amort = annualization_factor_debt_equity(**(financials or {}))
    dt_hours = float(time_step_hours)

    capital_kw: list[float] = []
    capital_m2: list[float] = []
    om_kw: list[float] = []
    om_m2: list[float] = []
    vom: list[float] = []
    density: list[float] = []
    unit_area: list[float] = []
    unit_kw: list[float] = []
    annual_cap_unit: list[float] = []
    om_unit: list[float] = []

    for idx, pk in enumerate(profiles):
        merged = _merge_params(params, _profile_overrides(profiles, params, pk))
        capital_kw.append(float(merged["capital_cost_per_kw"]))
        capital_m2.append(float(merged["capital_cost_per_m2"]))
        om_kw.append(float(merged["fixed_om_per_kw_year"]))
        om_m2.append(float(merged["fixed_om_per_m2_year"]))
        vom.append(float(merged["variable_om_per_kwh"]))
        density.append(float(merged["max_power_density_kw_per_m2"]))
        ua = float(merged["unit_swept_area_m2"])
        uk = float(merged["unit_capacity_kw"])
        if ua <= 0 or uk <= 0:
            raise ValueError(
                f"hydrokinetic: unit_swept_area_m2 and unit_capacity_kw must be > 0 for profile {pk!r}"
            )
        unit_area.append(ua)
        unit_kw.append(uk)
        cpu = merged.get("capital_cost_per_unit")
        if cpu is not None:
            annual_cap_unit.append(amort * float(cpu))
        else:
            annual_cap_unit.append(amort * capital_kw[-1] * uk)
        om_unit.append(float(merged["fixed_om_per_unit_year"]))

    if reference_swept_area_m2 <= 0:
        raise ValueError(
            "hydrokinetic: reference_swept_area_m2 must be > 0 (set data.static['hydrokinetic_reference_swept_area_m2'] "
            "or CaseConfig.hydrokinetic_reference_swept_area_m2 / load_hydrokinetic_into_container)."
        )
    if reference_kw <= 0:
        raise ValueError("hydrokinetic: reference_kw (data.static['hydrokinetic_reference_kw']) must be > 0")

    scale = reference_kw / reference_swept_area_m2
    yield_init: dict[tuple[str, int], float] = {}
    for pk in profiles:
        series = production_by_profile[pk]
        for t in time_indices:
            yield_init[(pk, t)] = float(series[t]) * scale

    raw_max_inst = params.get("max_installed_units_by_node_and_profile")
    if raw_max_inst is None:
        raw_max_inst = params.get("max_units_adopted_by_node_and_profile")
    max_installed_units = _int_dict(
        nodes,
        profiles,
        raw_max_inst,
        10**9,
        "max_installed_units",
    )
    existing_units = _int_dict(
        nodes,
        profiles,
        params.get("existing_units_by_node_and_profile"),
        0,
        "existing_units",
    )
    existing_area = _float_dict(
        nodes,
        profiles,
        params.get("existing_swept_area_m2_by_node_and_profile"),
        0.0,
        "existing_swept_area_m2",
    )
    existing_cap_kw = _float_dict(
        nodes,
        profiles,
        params.get("existing_capacity_kw_by_node_and_profile"),
        0.0,
        "existing_capacity_kw",
    )
    max_area = _float_dict(
        nodes,
        profiles,
        params.get("max_swept_area_m2_by_node_and_profile"),
        1.0e12,
        "max_swept_area_m2",
    )

    return ResolvedHydrokineticInputs(
        formulation=formulation,
        allow_adoption=allow_adoption,
        amortization_factor=amort,
        time_step_hours=float(time_step_hours),
        capital_cost_per_kw=capital_kw,
        capital_cost_per_m2=capital_m2,
        fixed_om_per_kw_year=om_kw,
        fixed_om_per_m2_year=om_m2,
        variable_om_per_kwh=vom,
        max_power_density_kw_per_m2=density,
        unit_swept_area_m2=unit_area,
        unit_capacity_kw=unit_kw,
        annual_capital_per_unit=annual_cap_unit,
        fixed_om_per_unit_year=om_unit,
        max_installed_units_by_node_profile=max_installed_units,
        existing_units_by_node_profile=existing_units,
        existing_swept_area_m2=existing_area,
        existing_capacity_kw=existing_cap_kw,
        max_swept_area_m2=max_area,
        yield_kwh_per_m2_init=yield_init,
    )
