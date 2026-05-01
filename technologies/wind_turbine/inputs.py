"""Wind turbine input defaults, validation, and parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity

DEFAULT_WIND_TURBINE_PARAMS = {
    "allow_adoption": True,
    "capital_cost_per_kw": 1800.0,
    "om_per_kw_year": 35.0,
    "existing_wind_capacity_by_node_and_profile": None,
    "existing_capital_recovery_per_kw_year": None,
    "use_marginal_capital_for_existing_recovery": False,
    "max_capacity_by_node_and_profile": None,
}


@dataclass
class ResolvedWindInputs:
    capital_list: list[float]
    om_list: list[float]
    existing_cap_recovery_per_kw: list[float]
    existing_init: dict[tuple[str, str], float]
    has_capacity_limits: bool
    capacity_limit_index: list[tuple[str, str]]
    max_capacity_by_node_profile: dict[tuple[str, str], float]
    amortization_factor: float


def _params_per_profile(
    wind_profiles: list[str],
    global_params: dict[str, Any],
) -> tuple[list[float], list[float]]:
    by_profile = global_params.get("params_by_profile")
    capital_list: list[float] = []
    om_list: list[float] = []

    for profile_idx, wind_profile_key in enumerate(wind_profiles):
        if by_profile is None:
            overrides = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(wind_profile_key) or {}).copy()
        elif isinstance(by_profile, list) and profile_idx < len(by_profile):
            overrides = (by_profile[profile_idx] or {}).copy()
        else:
            overrides = {}

        merged = {**global_params, **overrides}
        capital_list.append(float(merged["capital_cost_per_kw"]))
        om_list.append(float(merged["om_per_kw_year"]))

    return capital_list, om_list


def _existing_capital_recovery_per_kw_list(
    wind_profiles: list[str],
    global_params: dict[str, Any],
    capital_list: list[float],
    amortization_factor: float,
) -> list[float]:
    by_profile = global_params.get("params_by_profile")
    out: list[float] = []
    for profile_idx, wind_profile_key in enumerate(wind_profiles):
        if by_profile is None:
            overrides: dict[str, Any] = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(wind_profile_key) or {}).copy()
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


def _resolve_existing_capacity(
    nodes: list[str],
    wind_profiles: list[str],
    params: dict[str, Any],
) -> dict[tuple[str, str], float]:
    by_node_profile = params.get("existing_wind_capacity_by_node_and_profile") or {}
    out: dict[tuple[str, str], float] = {}
    for node in nodes:
        for profile in wind_profiles:
            value = 0.0
            if isinstance(by_node_profile.get(node), dict):
                value = float(by_node_profile[node].get(profile, 0.0))
            elif (node, profile) in by_node_profile:
                value = float(by_node_profile[(node, profile)])
            if value < 0:
                raise ValueError(
                    f"wind_turbine: existing_wind_capacity for (node={node!r}, profile={profile!r}) must be >= 0, got {value}. "
                    "Check existing_wind_capacity_by_node_and_profile in technology_parameters['wind_turbine']."
                )
            out[(node, profile)] = value
    return out


def resolve_wind_block_inputs(
    wind_turbine_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
    wind_profiles: list[str],
) -> ResolvedWindInputs:
    """Merge defaults with user overrides and resolve per-profile and per-node parameters."""
    params = (wind_turbine_params or {}).copy()
    for key, value in DEFAULT_WIND_TURBINE_PARAMS.items():
        params.setdefault(key, value)

    capital_list, om_list = _params_per_profile(wind_profiles, params)
    amortization_factor = annualization_factor_debt_equity(**(financials or {}))
    existing_cap_recovery_per_kw = _existing_capital_recovery_per_kw_list(
        wind_profiles,
        params,
        capital_list,
        amortization_factor,
    )

    capacity_raw = params.get("max_capacity_by_node_and_profile") or {}
    capacity_limit_index: list[tuple[str, str]] = []
    max_capacity_by_node_profile: dict[tuple[str, str], float] = {}
    for node in nodes:
        for profile in wind_profiles:
            value = None
            if isinstance(capacity_raw.get(node), dict):
                value = capacity_raw[node].get(profile)
            elif (node, profile) in capacity_raw:
                value = capacity_raw[(node, profile)]
            if value is not None:
                limit = float(value)
                if limit <= 0:
                    raise ValueError(
                        f"wind_turbine: max_capacity for (node={node!r}, profile={profile!r}) must be > 0, got {value}. "
                        "Check max_capacity_by_node_and_profile in technology_parameters['wind_turbine']."
                    )
                capacity_limit_index.append((node, profile))
                max_capacity_by_node_profile[(node, profile)] = limit

    return ResolvedWindInputs(
        capital_list=capital_list,
        om_list=om_list,
        existing_cap_recovery_per_kw=existing_cap_recovery_per_kw,
        existing_init=_resolve_existing_capacity(nodes, wind_profiles, params),
        has_capacity_limits=bool(capacity_limit_index),
        capacity_limit_index=capacity_limit_index,
        max_capacity_by_node_profile=max_capacity_by_node_profile,
        amortization_factor=amortization_factor,
    )
