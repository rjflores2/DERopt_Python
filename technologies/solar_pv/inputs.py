"""Solar PV input defaults, validation, and parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


DEFAULT_SOLAR_PV_PARAMS = {
    "allow_adoption": True,
    "efficiency": 0.2,
    "capital_cost_per_kw": 1500.0,
    "om_per_kw_year": 20.0,
    "max_capacity_area_by_node_and_profile": None,
    "existing_solar_capacity_by_node_and_profile": None,
    "existing_capital_recovery_per_kw_year": None,
    "use_marginal_capital_for_existing_recovery": False,
}


@dataclass
class ResolvedSolarInputs:
    """Parameter-derived inputs for the solar block (no time series)."""

    efficiency_list: list[float]
    capital_list: list[float]
    om_list: list[float]
    existing_cap_recovery_per_kw: list[float]
    existing_init: dict[tuple[str, str], float]
    has_area_limits: bool
    area_index: list[tuple[str, str]]
    max_capacity_area_by_node_profile: dict[tuple[str, str], float]
    amortization_factor: float


def _params_per_profile(
    solar_profiles: list[str],
    global_params: dict[str, Any],
) -> tuple[list[float], list[float], list[float]]:
    by_profile = global_params.get("params_by_profile")
    efficiency_list: list[float] = []
    capital_list: list[float] = []
    om_list: list[float] = []

    for profile_idx, solar_profile_key in enumerate(solar_profiles):
        if by_profile is None:
            overrides = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(solar_profile_key) or {}).copy()
        elif isinstance(by_profile, list) and profile_idx < len(by_profile):
            overrides = (by_profile[profile_idx] or {}).copy()
        else:
            overrides = {}

        merged = {**global_params, **overrides}
        efficiency_list.append(float(merged["efficiency"]))
        capital_list.append(float(merged["capital_cost_per_kw"]))
        om_list.append(float(merged["om_per_kw_year"]))

    return efficiency_list, capital_list, om_list


def _existing_capital_recovery_per_kw_list(
    solar_profiles: list[str],
    global_params: dict[str, Any],
    capital_list: list[float],
    amortization_factor: float,
) -> list[float]:
    by_profile = global_params.get("params_by_profile")
    out: list[float] = []
    for profile_idx, solar_profile_key in enumerate(solar_profiles):
        if by_profile is None:
            overrides: dict[str, Any] = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(solar_profile_key) or {}).copy()
        elif isinstance(by_profile, list) and profile_idx < len(by_profile):
            overrides = (by_profile[profile_idx] or {}).copy()
        else:
            overrides = {}
        merged = {**global_params, **overrides}
        explicit = merged.get("existing_capital_recovery_per_kw_year")
        use_marginal = bool(merged.get("use_marginal_capital_for_existing_recovery", False))
        cap_kw = capital_list[profile_idx]
        if explicit is not None:
            out.append(float(explicit))
        elif use_marginal:
            out.append(cap_kw * amortization_factor)
        else:
            out.append(0.0)
    return out


def _validate_solar_params(
    solar_profiles: list[str],
    efficiency_list: list[float],
) -> None:
    for profile_idx, efficiency in enumerate(efficiency_list):
        profile_label = (
            solar_profiles[profile_idx] if profile_idx < len(solar_profiles) else f"profile index {profile_idx}"
        )
        if efficiency <= 0 or efficiency > 1:
            raise ValueError(
                f"solar_pv: efficiency for {profile_label!r} must be in (0, 1], got {efficiency}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )


def _resolve_existing_capacity(
    nodes: list[str],
    solar_profiles: list[str],
    params: dict[str, Any],
) -> dict[tuple[str, str], float]:
    by_node_profile = params.get("existing_solar_capacity_by_node_and_profile") or {}
    out: dict[tuple[str, str], float] = {}
    for node in nodes:
        for profile in solar_profiles:
            value = 0.0
            if isinstance(by_node_profile.get(node), dict):
                value = float(by_node_profile[node].get(profile, 0.0))
            elif (node, profile) in by_node_profile:
                value = float(by_node_profile[(node, profile)])
            if value < 0:
                raise ValueError(
                    f"solar_pv: existing_solar_capacity for (node={node!r}, profile={profile!r}) must be >= 0, got {value}. "
                    "Check existing_solar_capacity_by_node_and_profile in technology_parameters['solar_pv']."
                )
            out[(node, profile)] = value
    return out


def resolve_solar_block_inputs(
    solar_pv_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
    solar_profiles: list[str],
) -> ResolvedSolarInputs:
    """Merge defaults with user overrides and resolve per-profile and per-node parameters."""
    params = (solar_pv_params or {}).copy()
    for key, value in DEFAULT_SOLAR_PV_PARAMS.items():
        params.setdefault(key, value)

    efficiency_list, capital_list, om_list = _params_per_profile(solar_profiles, params)
    _validate_solar_params(solar_profiles, efficiency_list)

    amortization_factor = annualization_factor_debt_equity(**(financials or {}))
    existing_cap_recovery_per_kw = _existing_capital_recovery_per_kw_list(
        solar_profiles,
        params,
        capital_list,
        amortization_factor,
    )

    area_raw = params.get("max_capacity_area_by_node_and_profile") or {}
    area_index: list[tuple[str, str]] = []
    max_capacity_area_by_node_profile: dict[tuple[str, str], float] = {}
    for node in nodes:
        for profile in solar_profiles:
            value = None
            if isinstance(area_raw.get(node), dict):
                value = area_raw[node].get(profile)
            elif (node, profile) in area_raw:
                value = area_raw[(node, profile)]
            if value is not None:
                area_value = float(value)
                if area_value <= 0:
                    raise ValueError(
                        f"solar_pv: max_capacity_area for (node={node!r}, profile={profile!r}) must be > 0, got {value}. "
                        "Check max_capacity_area_by_node_and_profile in technology_parameters['solar_pv']."
                    )
                area_index.append((node, profile))
                max_capacity_area_by_node_profile[(node, profile)] = area_value

    return ResolvedSolarInputs(
        efficiency_list=efficiency_list,
        capital_list=capital_list,
        om_list=om_list,
        existing_cap_recovery_per_kw=existing_cap_recovery_per_kw,
        existing_init=_resolve_existing_capacity(nodes, solar_profiles, params),
        has_area_limits=bool(area_index),
        area_index=area_index,
        max_capacity_area_by_node_profile=max_capacity_area_by_node_profile,
        amortization_factor=amortization_factor,
    )
