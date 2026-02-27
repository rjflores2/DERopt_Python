"""
Solar PV technology block.

Solar is modeled per node and per profile:
- Nodes: one per load (data.static["electricity_load_keys"]). Multi-node cases have
  multiple nodes; single-node has one. Each node can host its own solar capacity.
- Profiles: fixed, 1-D tracking, etc. (data.static["solar_production_keys"]). Each
  profile has its own capacity and generation at each node.

Decision variables: solar_capacity_adopted[node, profile], solar_generation[node, profile, t].
Example: 3 nodes × 2 technologies = 6 capacity variables, each with its own constraints.
Solar potential is the same profile time series at every node (no per-node irradiance yet).

Keep this block simple and well commented—technology components are a main place
for manual programming and must stay transparent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from shared.financials import annualization_factor_debt_equity


# -----------------------------------------------------------------------------
# Default parameters (used when solar_pv_params does not supply overrides)
# -----------------------------------------------------------------------------

DEFAULT_SOLAR_PV_PARAMS = {
    "efficiency": 0.2,                  # Solar efficiency (output / ~1 kW/m² input); 0..1. Area proxy = capacity/efficiency.
    "capital_cost_per_kw": 1500.0,      # $/kW (one-time)
    "om_per_kw_year": 20.0,             # $/kW-year O&M
    # Area and existing capacity are per (node, profile). Defaults: no area limit, 0 existing.
    # Area limits come from max_capacity_area_by_node_and_profile in solar_pv_params:
    #   {(node_key, profile_key): area} or {node_key: {profile_key: area}}.
    "max_capacity_area_by_node_and_profile": None,
    # Existing capacity comes from existing_solar_capacity_by_node_and_profile:
    #   {(node_key, profile_key): kW} or {node_key: {profile_key: kW}}.
    "existing_solar_capacity_by_node_and_profile": None,
}

# Per-profile overrides: set solar_pv_params["params_by_profile"] to a list in the same
# order as data.static["solar_production_keys"] (first entry = first profile, etc.).
# See README "Technology parameters (solar)" for an example.
# Existing capacity is per (node, profile); use existing_solar_capacity_by_node_and_profile.


# -----------------------------------------------------------------------------
# Block builder (main entry)
# -----------------------------------------------------------------------------

def add_solar_pv_block(
    model: Any,
    data: Any,
    *,
    solar_pv_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Solar PV block to the model.

    Solar is optimized at each node (one per load) and for each profile (e.g. fixed, 1-D tracking).
    Same solar potential profile is used at every node (no per-node irradiance data yet).

    Data used:
        - model.T, model.NODES          -> time set and node set (created in core from data)
        - data.static["solar_production_keys"], data.timeseries["solar_production__*"]
          -> one potential (kWh/kW) per profile per time step
        - solar_pv_params   -> optional overrides; params_by_profile for per-profile params

    Block contents:
        - Sets: NODES, SOLAR (profiles)
        - Params: solar_potential[profile, t], efficiency[profile], capital_cost_per_kw[profile],
          om_per_kw_year[profile], existing_solar_capacity[node, profile];
          max_capacity_area[node, profile] only if set (per-node, per-technology area limits)
        - Vars: solar_capacity_adopted[node, profile], solar_generation[node, profile, t]
        - Constraints: generation[node,profile,t] <= (existing + adopted) * potential
          (solar_potential is already effective output per kW nameplate);
          if max_capacity_area_by_node_and_profile is set: per (node, profile),
          (existing+adopted)/efficiency[profile] <= area_limit[node, profile]
        - objective_contribution: annual cost summed over nodes and profiles
        - electricity_supply_term[node, t]: sum over profiles of solar_generation[node, profile, t] for balance at that node
    """
    # ----- Indices and time series from data -----
    T = model.T
    NODES = list(model.NODES)  # model.NODES is created in core from electricity_load_keys

    solar_keys = data.static.get("solar_production_keys") or [] # Get the solar production keys, which are data labels in the data structure for each individual solar technology
    if not solar_keys: # If there are no solar production keys, raise an error - THERE HAS TO BE A SOLAR TECHNOLOGY! OBVI!!
        raise ValueError("solar_pv block requires data.static['solar_production_keys'] (load solar data first)")
    SOLAR = list(solar_keys) # Create a list of solar production keys
    production_by_profile = {key: list(data.timeseries[key]) for key in SOLAR} # Create a dictionary of production by profile, which is the time series data for each individual solar technology, this is a list of floats

    # ----- Resolved technology parameters (defaults + overrides) -----
    r = _resolve_solar_block_inputs(solar_pv_params, financials, NODES, SOLAR) # Resolve the technology parameters for the solar PV technology

    # ----- Block rule -----
    # Use model.NODES (from core) for node indexing; only SOLAR is block-local.
    _nodes = model.NODES # Use model.NODES (from core) for node indexing; only b.SOLAR is block-local.

    def block_rule(b):
        b.SOLAR = pyo.Set(initialize=SOLAR, ordered=True) # Create a set of solar technologies

        b.solar_potential = pyo.Param(
            b.SOLAR, T,
            initialize={(p, t): production_by_profile[p][t] for p in SOLAR for t in T},
            within=pyo.NonNegativeReals,
            mutable=True,
        ) # Create a parameter for the solar potential, which is the time series data for each individual solar technology, this is a list of floats
        b.efficiency = pyo.Param(
            b.SOLAR,
            initialize={p: r.efficiency_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.capital_cost_per_kw = pyo.Param(
            b.SOLAR,
            initialize={p: r.capital_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.om_per_kw_year = pyo.Param(
            b.SOLAR,
            initialize={p: r.om_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.existing_solar_capacity = pyo.Param(
            _nodes, b.SOLAR,
            initialize=r.existing_init,
            within=pyo.NonNegativeReals, mutable=True,
        )
        if r.has_area_limits:
            # Area limits are defined only for (node, profile) pairs present in area_index.
            b.AREA_LIMIT_INDEX = pyo.Set(
                dimen=2, initialize=r.area_index, ordered=True
            )
            b.max_capacity_area = pyo.Param(
                b.AREA_LIMIT_INDEX,
                initialize=r.max_capacity_area_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=True,
            )

        b.solar_capacity_adopted = pyo.Var(_nodes, b.SOLAR, within=pyo.NonNegativeReals)
        b.solar_generation = pyo.Var(_nodes, b.SOLAR, T, within=pyo.NonNegativeReals)

        def generation_limits_rule(m, node, profile, t):
            return m.solar_generation[node, profile, t] <= (
                (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                * m.solar_potential[profile, t]
            )
        b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule)

        if r.has_area_limits:
            def capacity_area_cap_rule(m, node, profile):
                # Each (node, profile) with a specified area limit has its own constraint.
                # Area proxy = capacity/efficiency (efficiency = output / ~1 kW/m² input).
                return (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    / m.efficiency[profile]
                ) <= m.max_capacity_area[node, profile]
            b.capacity_area_cap = pyo.Constraint(
                b.AREA_LIMIT_INDEX, rule=capacity_area_cap_rule
            )

        b.objective_contribution = sum(
            b.capital_cost_per_kw[p] * b.solar_capacity_adopted[n, p] * r.amortization_factor
            + b.om_per_kw_year[p] * b.solar_capacity_adopted[n, p]
            for n in _nodes for p in b.SOLAR
        )
        b.electricity_supply_term = pyo.Expression(
            _nodes, T,
            rule=lambda m, n, t: sum(m.solar_generation[n, p, t] for p in m.SOLAR),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv


def register(
    model: Any,
    data: Any,
    *,
    solar_pv_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """
    Attach the Solar PV block if solar data is present; otherwise do nothing.

    Returns the block if attached, None otherwise.
    """
    if not data.static.get("solar_production_keys"):
        return None
    return add_solar_pv_block(
        model,
        data,
        solar_pv_params=solar_pv_params,
        financials=financials,
    )


# -----------------------------------------------------------------------------
# Plumbing (helpers used by add_solar_pv_block)
# -----------------------------------------------------------------------------

def _params_per_profile(solar_keys: list, global_params: dict) -> tuple[list, list, list]:
    """
    Purpose
    -------
    The model has one solar "profile" per technology (e.g. fixed, 1-D tracking). Each
    profile can have different technical/economic parameters. This function turns the
    user's config (global defaults + optional per-profile overrides) into three lists,
    one value per profile in solar_keys order. The block builder then uses these lists
    to initialize Pyomo Params indexed by profile (efficiency[p], capital_cost_per_kw[p],
    om_per_kw_year[p]), which are the same at every node.

    Inputs
    ------
    solar_keys : list of profile IDs (e.g. from data.static["solar_production_keys"]).
    global_params : solar_pv_params already merged with DEFAULT_SOLAR_PV_PARAMS.

    Per-profile overrides live in global_params["params_by_profile"]:
    - None or missing : use global values for all profiles.
    - Dict keyed by profile string : overrides by name (e.g. "solar_production__fixed_kw_kw").
    - List in same order as solar_keys : overrides by position (first list entry = first profile).

    Returns
    -------
    (efficiency_list, capital_list, om_list), each of length len(solar_keys), in profile order.

    Existing capacity is not handled here; it is per (node, profile) and is set in the
    block from existing_solar_capacity_by_node_and_profile (default 0 at every node).
    """
    by_profile = global_params.get("params_by_profile")

    efficiency_list = []
    capital_list = []
    om_list = []

    for i, key in enumerate(solar_keys):
        # Per-profile override: by key or by index
        if by_profile is None:
            overrides = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(key) or {}).copy()
        elif isinstance(by_profile, list) and i < len(by_profile):
            overrides = (by_profile[i] or {}).copy()
        else:
            overrides = {}

        p = {**global_params, **overrides}
        efficiency_list.append(float(p["efficiency"]))
        capital_list.append(float(p["capital_cost_per_kw"]))
        om_list.append(float(p["om_per_kw_year"]))

    return efficiency_list, capital_list, om_list


def _resolve_existing_capacity(
    nodes: list[str],
    solar: list[str],
    params: dict[str, Any],
) -> dict[tuple[str, str], float]:
    """
    Build existing_solar_capacity initializer: (node, profile) -> kW.
    Default 0 everywhere; non-zero only from params["existing_solar_capacity_by_node_and_profile"]
    (nested {node: {profile: kW}} or flat {(node, profile): kW}).
    """
    by_node_profile = params.get("existing_solar_capacity_by_node_and_profile") or {}
    out: dict[tuple[str, str], float] = {}
    for n in nodes:
        for p in solar:
            val = 0.0
            if isinstance(by_node_profile.get(n), dict):
                val = float(by_node_profile[n].get(p, 0.0))
            elif (n, p) in by_node_profile:
                val = float(by_node_profile[(n, p)])
            out[(n, p)] = val
    return out


@dataclass
class _ResolvedSolarInputs:
    """All parameter-derived inputs for the solar block (no time series)."""

    efficiency_list: list[float]
    capital_list: list[float]
    om_list: list[float]
    existing_init: dict[tuple[str, str], float]
    has_area_limits: bool
    area_index: list[tuple[str, str]]  # list of (node, profile) pairs with area limits
    max_capacity_area_by_node_profile: dict[tuple[str, str], float]
    amortization_factor: float


def _resolve_solar_block_inputs(
    solar_pv_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
    solar: list[str],
) -> _ResolvedSolarInputs:
    """
    Merge defaults with user overrides and resolve per-profile and per-node params.
    Returns a single object used by add_solar_pv_block to build the Pyomo block.
    """
    params = (solar_pv_params or {}).copy()
    for k, v in DEFAULT_SOLAR_PV_PARAMS.items():
        params.setdefault(k, v)

    efficiency_list, capital_list, om_list = _params_per_profile(solar, params)

    # Per-node, per-profile area limits: {(node, profile): area} or {node: {profile: area}}.
    area_raw = params.get("max_capacity_area_by_node_and_profile") or {}
    area_index: list[tuple[str, str]] = []
    max_capacity_area_by_node_profile: dict[tuple[str, str], float] = {}
    for n in nodes:
        for p in solar:
            val = None
            if isinstance(area_raw.get(n), dict):
                val = area_raw[n].get(p)
            elif (n, p) in area_raw:
                val = area_raw[(n, p)]
            if val is None:
                continue
            if val < 0:
                continue
            pair = (n, p)
            area_index.append(pair)
            max_capacity_area_by_node_profile[pair] = float(val)

    has_area_limits = bool(area_index)

    existing_init = _resolve_existing_capacity(nodes, solar, params)

    fin = financials or {}
    amortization_factor = annualization_factor_debt_equity(**fin)

    return _ResolvedSolarInputs(
        efficiency_list=efficiency_list,
        capital_list=capital_list,
        om_list=om_list,
        existing_init=existing_init,
        has_area_limits=has_area_limits,
        area_index=area_index,
        max_capacity_area_by_node_profile=max_capacity_area_by_node_profile,
        amortization_factor=amortization_factor,
    )
