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
    "allow_adoption": True,              # If False, only existing capacity is modeled (no adoption variable); default True.
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
        - electricity_source_term[node, t]: sum over profiles of solar_generation[node, profile, t] (source for balance)
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

    # Allow adoption of new solar capacity
    allow_adoption = (solar_pv_params or {}).get("allow_adoption", True) # If "true", then the model will allow the adoption of new solar capacity, otherwise it will only use existing solar capacity

    # ----- Block rule -----
    # Use model.NODES (from core) for node indexing; only SOLAR is block-local.
    _nodes = model.NODES # Use model.NODES (from core) for node indexing; only b.SOLAR is block-local.

    def block_rule(b):
        b.SOLAR = pyo.Set(initialize=SOLAR, ordered=True) # Create a set of solar technologies

        b.solar_potential = pyo.Param( # Create a parameter for the solar potential [kWh production/kW Capacity], which is the time series data for each individual solar technology, this is a list of floats
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
        b.capital_cost_per_kw = pyo.Param( # Capital cost [$/kW]
            b.SOLAR,
            initialize={p: r.capital_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.om_per_kw_year = pyo.Param( #Annual O&M [$/kW Capacity]
            b.SOLAR,
            initialize={p: r.om_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.existing_solar_capacity = pyo.Param( # Existing solar capacity [kW]
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

        b.solar_generation = pyo.Var(_nodes, b.SOLAR, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            b.solar_capacity_adopted = pyo.Var(_nodes, b.SOLAR, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t): # Constraint on the solar generation, which is the production of the solar technology at the node and profile at the time step
                return m.solar_generation[node, profile, t] <= (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    * m.solar_potential[profile, t]
                )
            b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule) # Adding solar generation constraint to the pyomo model

            if r.has_area_limits: # If there are area limits, then add a constraint on the solar capacity, which is the area of the solar technology at the node and profile
                def capacity_area_cap_rule(m, node, profile):
                    return (
                        (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                        / m.efficiency[profile]
                    ) <= m.max_capacity_area[node, profile]
                b.capacity_area_cap = pyo.Constraint( # Adding solar capacity area constraint to the pyomo model
                    b.AREA_LIMIT_INDEX, rule=capacity_area_cap_rule
                )

            # Objective: capital (annualized) on adopted + O&M on total capacity (existing + adopted).
            # O&M on existing is included so the objective equals total annual cost for reporting.
            b.objective_contribution = sum(
                b.capital_cost_per_kw[p] * b.solar_capacity_adopted[n, p] * r.amortization_factor
                + b.om_per_kw_year[p] * (b.existing_solar_capacity[n, p] + b.solar_capacity_adopted[n, p])
                for n in _nodes for p in b.SOLAR
            )
            # For post-processing: annual cost from existing assets (sunk; does not affect optimum).
            # Include O&M on existing; add remaining debt/capital recovery on existing when we have that data.
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    for n in _nodes for p in b.SOLAR
                )
            )
        else:
            # Existing-only: no adoption variable; generation and costs from existing capacity only.
            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    m.existing_solar_capacity[node, profile] * m.solar_potential[profile, t]
                )
            b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule_existing_only)

            # No area cap in existing-only mode: existing capacity is given and assumed to fit.

            b.objective_contribution = sum(
                b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                for n in _nodes for p in b.SOLAR
            )
            # Same reporting: cost from existing assets; here it's O&M only (add debt when we have it).
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    for n in _nodes for p in b.SOLAR
                )
            )

        b.electricity_source_term = pyo.Expression(
            _nodes, T,
            rule=lambda m, n, t: sum(m.solar_generation[n, p, t] for p in m.SOLAR),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv


def register( # Register adds the solar PV pyomo block is there if solar data is present, otherwise it returns None
    model: Any,
    data: Any,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """
    Attach the Solar PV block if solar data is present; otherwise do nothing.

    Called by core from the technology registry. Params are read from
    technology_parameters["solar_pv"]. Returns the block if attached, None otherwise.
    """
    if not data.static.get("solar_production_keys"): # If there is no solar production keys, then return None
        return None
    solar_pv_params = (technology_parameters or {}).get("solar_pv") or {} #Else get the solar PV parameters from either the user input in the config file, or the default parameters
    return add_solar_pv_block( # Add the solar PV pyomo block to the model
        model,
        data,
        solar_pv_params=solar_pv_params, # Pass the solar PV parameters to the add_solar_pv_block function
        financials=financials or {}, # Pass the financials to the add_solar_pv_block function
    )


# -----------------------------------------------------------------------------
# Plumbing (helpers used by add_solar_pv_block)
# -----------------------------------------------------------------------------

def _params_per_profile(solar_keys: list, global_params: dict) -> tuple[list, list, list]:
    """
    Purpose
    -------
    The model has one solar "profile" per technology (e.g. fixed, 1-D tracking technology
     each have a cost, efficiency, etc.). Each
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
    by_profile = global_params.get("params_by_profile") #Global parameters for the solar PV technology, which are inputs to build_solar_pv_block function   this is a list of dictionaries, each dictionary contains the efficiency, capital cost, and O&M cost for a single solar technology   

    efficiency_list = [] # This is a list of efficiencies for each profile, which is the efficiency of the solar technology at the node and profile at the time step
    capital_list = [] # This is a list of capital costs for each profile, which is the capital cost of the solar technology at the node and profile at the time step
    om_list = [] # This is a list of O&M costs for each profile, which is the O&M cost of the solar technology at the node and profile at the time step

    for i, key in enumerate(solar_keys): # Looping through the solar keys, which are the headers from the solar resource time series data
        # Per-profile override: by key or by index
        if by_profile is None: # If there are no global parameters for the solar PV technology, then set the overrides to an empty dictionary
            overrides = {}
        elif isinstance(by_profile, dict): # If the global parameters for the solar PV technology are a dictionary, then set the overrides to the dictionary for the key
            overrides = (by_profile.get(key) or {}).copy()
        elif isinstance(by_profile, list) and i < len(by_profile): # If the global parameters for the solar PV technology are a list, then set the overrides to the list for the index
            overrides = (by_profile[i] or {}).copy()
        else:
            overrides = {} # If there are no global parameters for the solar PV technology, then set the overrides to an empty dictionary

        p = {**global_params, **overrides} # This is a dictionary of the global parameters for the solar PV technology and the overrides for the profile
        efficiency_list.append(float(p["efficiency"])) # This is a list of efficiencies for each profile, which is the efficiency of the solar technology at the node and profile at the time step
        capital_list.append(float(p["capital_cost_per_kw"])) # This is a list of capital costs for each profile, which is the capital cost of the solar technology at the node and profile at the time step
        om_list.append(float(p["om_per_kw_year"])) # This is a list of O&M costs for each profile, which is the O&M cost of the solar technology at the node and profile at the time step

    return efficiency_list, capital_list, om_list


def _validate_solar_params(
    solar_keys: list[str],
    efficiency_list: list[float],
    capital_list: list[float],
    om_list: list[float],
) -> None:
    """Raise ValueError if any solar_pv parameter is invalid (e.g. efficiency 0 or negative)."""
    for i, (eff, cap, om) in enumerate(zip(efficiency_list, capital_list, om_list)):
        profile_label = solar_keys[i] if i < len(solar_keys) else f"profile index {i}"
        if eff <= 0 or eff > 1:
            raise ValueError(
                f"solar_pv: efficiency for {profile_label!r} must be in (0, 1], got {eff}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )
        if cap < 0:
            raise ValueError(
                f"solar_pv: capital_cost_per_kw for {profile_label!r} must be >= 0, got {cap}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )
        if om < 0:
            raise ValueError(
                f"solar_pv: om_per_kw_year for {profile_label!r} must be >= 0, got {om}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )


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
            if val < 0:
                raise ValueError(
                    f"solar_pv: existing_solar_capacity for (node={n!r}, profile={p!r}) must be >= 0, got {val}. "
                    "Check existing_solar_capacity_by_node_and_profile in technology_parameters['solar_pv']."
                )
            out[(n, p)] = val
    return out


@dataclass
class _ResolvedSolarInputs: #This data class is used to store the resolved solar block inputs for the solar PV technology
    """All parameter-derived inputs for the solar block (no time series)."""

    efficiency_list: list[float] # This is a list of efficiencies for each profile, which is the efficiency of the solar technology at the node and profile at the time step
    capital_list: list[float] # This is a list of capital costs for each profile, which is the capital cost of the solar technology at the node and profile at the time step
    om_list: list[float] # This is a list of O&M costs for each profile, which is the O&M cost of the solar technology at the node and profile at the time step
    existing_init: dict[tuple[str, str], float] # This is a dictionary of the existing solar capacity for each node and profile, which is the existing solar capacity of the solar technology at the node and profile at the time step
    has_area_limits: bool # This is a boolean flag to indicate if there are area limits for the solar PV technology
    area_index: list[tuple[str, str]]  # list of (node, profile) pairs with area limits
    max_capacity_area_by_node_profile: dict[tuple[str, str], float] # This is a dictionary of the maximum capacity area for each node and profile, which is the maximum capacity area of the solar technology at the node and profile at the time step
    amortization_factor: float # This is the amortization factor for the solar PV technology, which is the amortization factor for the solar PV technology


def _resolve_solar_block_inputs( #Merges default parameters with user inputs and resolves the per-profile and per-node parameters for the solar PV technology
    solar_pv_params: dict[str, Any] | None, # This is the solar PV parameters for the solar PV technology
    financials: dict[str, Any] | None, # This is the financials for the solar PV technology
    nodes: list[str], # This is the list of nodes for the solar PV technology
    solar: list[str], # This is the list of solar technologies for the solar PV technology
) -> _ResolvedSolarInputs: # This is the resolved solar block inputs for the solar PV technology
    """
    Merge defaults with user overrides and resolve per-profile and per-node parameters.
    Returns a single object used by add_solar_pv_block to build the Pyomo block.
    """
    params = (solar_pv_params or {}).copy() # This is a dictionary of the solar PV parameters for the solar PV technology
    for k, v in DEFAULT_SOLAR_PV_PARAMS.items(): # This is a dictionary of the default solar PV parameters for the solar PV technology
        params.setdefault(k, v)

    efficiency_list, capital_list, om_list = _params_per_profile(solar, params)
    _validate_solar_params(solar, efficiency_list, capital_list, om_list)

    # Per-node, per-profile area limits: {(node, profile): area} or {node: {profile: area}}.
    area_raw = params.get("max_capacity_area_by_node_and_profile") or {} # This is a dictionary of the maximum capacity area for each node and profile
    area_index: list[tuple[str, str]] = [] # This is a list of (node, profile) pairs with area limits      
    max_capacity_area_by_node_profile: dict[tuple[str, str], float] = {} # This is a dictionary of the maximum capacity area for each node and profile
    for n in nodes:
        for p in solar:
            val = None
            if isinstance(area_raw.get(n), dict):
                val = area_raw[n].get(p)
            elif (n, p) in area_raw:
                val = area_raw[(n, p)]
            if val is not None:
                area_val = float(val)
                if area_val <= 0:
                    raise ValueError(
                        f"solar_pv: max_capacity_area for (node={n!r}, profile={p!r}) must be > 0, got {val}. "
                        "Check max_capacity_area_by_node_and_profile in technology_parameters['solar_pv']."
                    )
                area_index.append((n, p))
                max_capacity_area_by_node_profile[(n, p)] = area_val

    has_area_limits = bool(area_index)

    existing_init = _resolve_existing_capacity(nodes, solar, params) # This is a dictionary of the existing solar capacity for each node and profile

    fin = financials or {} # This is a dictionary of the financials for the solar PV technology
    amortization_factor = annualization_factor_debt_equity(**fin) # This is the amortization factor for the solar PV technology

    return _ResolvedSolarInputs( # This is the resolved solar block inputs for the solar PV technology  
        efficiency_list=efficiency_list, # This is a list of efficiencies for each profile, which is the efficiency of the solar technology at the node and profile at the time step
        capital_list=capital_list, # This is a list of capital costs for each profile, which is the capital cost of the solar technology at the node and profile at the time step
        om_list=om_list, # This is a list of O&M costs for each profile, which is the O&M cost of the solar technology at the node and profile at the time step
        existing_init=existing_init, # This is a dictionary of the existing solar capacity for each node and profile, which is the existing solar capacity of the solar technology at the node and profile at the time step
        has_area_limits=has_area_limits,
        area_index=area_index, # This is a list of (node, profile) pairs with area limits
        max_capacity_area_by_node_profile=max_capacity_area_by_node_profile, # This is a dictionary of the maximum capacity area for each node and profile, which is the maximum capacity area of the solar technology at the node and profile at the time step
        amortization_factor=amortization_factor, # This is the amortization factor for the solar PV technology, which is the amortization factor for the solar PV technology       
    )
