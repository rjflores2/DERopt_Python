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
    # Per-node resource override. Default is broadcast (same time series everywhere, matching
    # pre-multi-resource behavior — correct for co-located nodes / single-site microgrids).
    # Shape: dict[node, dict[profile_key, resource_timeseries_key]]
    #        or equivalently dict[(node, profile_key), resource_timeseries_key].
    # Only (node, profile) pairs that need a non-default time series must appear; any missing
    # pair falls back to ``data.timeseries[profile_key]`` (the profile's canonical series).
    # Use this when nodes are geographically separated enough that climate / microclimate /
    # latitude produce materially different irradiance profiles.
    "solar_resource_assignment_by_node_and_profile": None,
}


@dataclass
class ResolvedSolarInputs:
    """Parameter-derived inputs for the solar block (no time series)."""

    efficiency_list: dict[str, float]
    capital_list: dict[str, float]
    om_list: dict[str, float]
    existing_cap_recovery_per_kw: dict[str, float]
    existing_init: dict[tuple[str, str], float]
    has_area_limits: bool
    area_index: list[tuple[str, str]]
    max_capacity_area_by_node_profile: dict[tuple[str, str], float]
    amortization_factor: float
    # Only (node, profile) pairs with an explicit override are present. Empty dict means
    # "broadcast the canonical per-profile time series to every node" (default behavior).
    resource_assignment_by_node_profile: dict[tuple[str, str], str]


def _get_profile_overrides(
    by_profile: dict | list | None,
    profile_idx: int,
    profile_key: str,
) -> dict[str, Any]:
    if by_profile is None:
        return {}
    if isinstance(by_profile, dict):
        return (by_profile.get(profile_key) or {}).copy()
    if isinstance(by_profile, list) and profile_idx < len(by_profile):
        return (by_profile[profile_idx] or {}).copy()
    return {}


def _params_per_profile(
    solar_profiles: list[str],
    global_params: dict[str, Any],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    by_profile = global_params.get("params_by_profile")
    efficiency_list: dict[str, float] = {}
    capital_list: dict[str, float] = {}
    om_list: dict[str, float] = {}

    for profile_idx, solar_profile_key in enumerate(solar_profiles):
        overrides = _get_profile_overrides(by_profile, profile_idx, solar_profile_key)
        merged = {**global_params, **overrides}
        efficiency_list[solar_profile_key] = float(merged["efficiency"])
        capital_list[solar_profile_key] = float(merged["capital_cost_per_kw"])
        om_list[solar_profile_key] = float(merged["om_per_kw_year"])

    return efficiency_list, capital_list, om_list


def _existing_capital_recovery_per_kw_list(
    solar_profiles: list[str],
    global_params: dict[str, Any],
    capital_list: dict[str, float],
    amortization_factor: float,
) -> dict[str, float]:
    by_profile = global_params.get("params_by_profile")
    out: dict[str, float] = {}
    for profile_idx, solar_profile_key in enumerate(solar_profiles):
        overrides = _get_profile_overrides(by_profile, profile_idx, solar_profile_key)
        merged = {**global_params, **overrides}
        explicit = merged.get("existing_capital_recovery_per_kw_year")
        use_marginal = bool(merged.get("use_marginal_capital_for_existing_recovery", False))
        cap_kw = capital_list[solar_profile_key]
        if explicit is not None:
            out[solar_profile_key] = float(explicit)
        elif use_marginal:
            out[solar_profile_key] = cap_kw * amortization_factor
        else:
            out[solar_profile_key] = 0.0
    return out


def _validate_solar_params(
    solar_profiles: list[str],
    efficiency_list: dict[str, float],
) -> None:
    for profile in solar_profiles:
        efficiency = efficiency_list[profile]
        if efficiency <= 0 or efficiency > 1:
            raise ValueError(
                f"solar_pv: efficiency for {profile!r} must be in (0, 1], got {efficiency}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )


def _resolve_resource_assignment(
    nodes: list[str],
    solar_profiles: list[str],
    params: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """Validate the optional per-(node, profile) resource override and return only the
    explicitly-set entries. Missing entries are left out so the block falls back to the
    canonical per-profile time series (broadcast behavior) without extra work.

    Accepts either nested-dict (``{node: {profile: key}}``) or flat tuple-keyed
    (``{(node, profile): key}``) input, matching the other solar *_by_node_and_profile
    inputs.
    """
    raw = params.get("solar_resource_assignment_by_node_and_profile") or {}
    out: dict[tuple[str, str], str] = {}
    nodes_set = set(nodes)
    profiles_set = set(solar_profiles)

    if isinstance(raw, dict):
        for outer_key, outer_value in raw.items():
            if isinstance(outer_value, dict):
                if outer_key not in nodes_set:
                    raise ValueError(
                        f"solar_pv: solar_resource_assignment_by_node_and_profile references "
                        f"unknown node {outer_key!r}; known nodes: {sorted(nodes_set)!r}."
                    )
                for profile_key, resource_key in outer_value.items():
                    if profile_key not in profiles_set:
                        raise ValueError(
                            f"solar_pv: solar_resource_assignment_by_node_and_profile[{outer_key!r}] "
                            f"references unknown profile {profile_key!r}; "
                            f"known profiles: {sorted(profiles_set)!r}."
                        )
                    if not isinstance(resource_key, str) or not resource_key:
                        raise ValueError(
                            f"solar_pv: resource key for (node={outer_key!r}, profile={profile_key!r}) "
                            f"must be a non-empty string timeseries key; got {resource_key!r}."
                        )
                    out[(outer_key, profile_key)] = resource_key
            elif isinstance(outer_key, tuple) and len(outer_key) == 2:
                node, profile_key = outer_key
                if node not in nodes_set:
                    raise ValueError(
                        f"solar_pv: solar_resource_assignment_by_node_and_profile references "
                        f"unknown node {node!r}; known nodes: {sorted(nodes_set)!r}."
                    )
                if profile_key not in profiles_set:
                    raise ValueError(
                        f"solar_pv: solar_resource_assignment_by_node_and_profile references "
                        f"unknown profile {profile_key!r}; known profiles: {sorted(profiles_set)!r}."
                    )
                if not isinstance(outer_value, str) or not outer_value:
                    raise ValueError(
                        f"solar_pv: resource key for (node={node!r}, profile={profile_key!r}) "
                        f"must be a non-empty string timeseries key; got {outer_value!r}."
                    )
                out[(node, profile_key)] = outer_value
    return out


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

    existing_init = _resolve_existing_capacity(nodes, solar_profiles, params)
    for node, profile in area_index:
        existing = existing_init[(node, profile)]
        limit = max_capacity_area_by_node_profile[(node, profile)]
        if existing > limit:
            raise ValueError(
                f"solar_pv: existing_solar_capacity at (node={node!r}, profile={profile!r}) is {existing} kW, "
                f"which exceeds max_capacity_area limit {limit}. "
                "Lower existing capacity or raise max_capacity_area_by_node_and_profile."
            )

    return ResolvedSolarInputs(
        efficiency_list=efficiency_list,
        capital_list=capital_list,
        om_list=om_list,
        existing_cap_recovery_per_kw=existing_cap_recovery_per_kw,
        existing_init=existing_init,
        has_area_limits=bool(area_index),
        area_index=area_index,
        max_capacity_area_by_node_profile=max_capacity_area_by_node_profile,
        amortization_factor=amortization_factor,
        resource_assignment_by_node_profile=_resolve_resource_assignment(
            nodes, solar_profiles, params
        ),
    )
