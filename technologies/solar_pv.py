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

from typing import Any

import pyomo.environ as pyo

from shared.financials import annualization_factor_debt_equity


# -----------------------------------------------------------------------------
# Default parameters (used when data.tech_params["solar_pv"] does not supply them)
# -----------------------------------------------------------------------------

DEFAULT_SOLAR_PV_PARAMS = {
    "efficiency": 0.2,                  # Derating (soiling, inverter losses); 0..1
    "capital_cost_per_kw": 1500.0,      # $/kW (one-time)
    "om_per_kw_year": 20.0,             # $/kW-year O&M
    "max_capacity_area": None,          # Per-node footprint: at each node, sum over profiles of (capacity/efficiency) <= this; None = no limit
    # Existing capacity is per (node, profile). Default is 0 at every node.
    # Set existing_solar_capacity_by_node_and_profile to specify where existing solar is:
    #   {(node_key, profile_key): kW} or {node_key: {profile_key: kW}}
    "existing_solar_capacity_by_node_and_profile": None,  # None or {} = 0 everywhere
}

# -----------------------------------------------------------------------------
# Multiple solar technologies (e.g. fixed vs 1-D tracking)
# -----------------------------------------------------------------------------
# When you have more than one solar profile, each can have its own efficiency,
# capital cost, and O&M. Set data.tech_params["solar_pv"]
# and add "params_by_profile" in one of two ways:
#
# Option A — By profile key (use the same keys as in data.static["solar_production_keys"]):
#
#   data.tech_params["solar_pv"] = {
#       "max_capacity_area": 10_000,
#       "params_by_profile": {
#           "solar_production__fixed_kw_kw": {
#               "efficiency": 0.20,
#               "capital_cost_per_kw": 1500,
#               "om_per_kw_year": 18,
#           },
#           "solar_production__1d_tracking_kw_kw": {
#               "efficiency": 0.22,
#               "capital_cost_per_kw": 2100,
#               "om_per_kw_year": 24,
#           },
#       },
#   }
#
# Option B — By load order (first dict = first profile, second = second, etc.):
#
#   data.tech_params["solar_pv"] = {
#       "max_capacity_area": 10_000,
#       "params_by_profile": [
#           {"efficiency": 0.20, "capital_cost_per_kw": 1500, "om_per_kw_year": 18},   # fixed
#           {"efficiency": 0.22, "capital_cost_per_kw": 2100, "om_per_kw_year": 24},   # 1-D tracking
#       ],
#   }
#
# You can override only some fields per profile; the rest come from defaults or
# from the top-level tech_params["solar_pv"] (e.g. "max_capacity_area" applies to all).
#
# Existing capacity is NOT set per-profile here. It is per (node, profile). Use
# existing_solar_capacity_by_node_and_profile in tech_params["solar_pv"] to specify
# which nodes have existing solar (default 0 at every node).


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
    global_params : tech_params["solar_pv"] already merged with DEFAULT_SOLAR_PV_PARAMS.

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


# -----------------------------------------------------------------------------
# Block builder
# -----------------------------------------------------------------------------

def add_solar_pv_block(model: Any, data: Any) -> pyo.Block:
    """
    Build and attach the Solar PV block to the model.

    Solar is optimized at each node (one per load) and for each profile (e.g. fixed, 1-D tracking).
    Same solar potential profile is used at every node (no per-node irradiance data yet).

    Data used:
        - data.indices["time"]          -> time set T
        - data.static["electricity_load_keys"] -> node set (one node per load)
        - data.static["solar_production_keys"], data.timeseries["solar_production__*"]
          -> one potential (kWh/kW) per profile per time step
        - data.tech_params["solar_pv"]   -> optional overrides; params_by_profile for per-profile params

    Block contents:
        - Sets: NODES, SOLAR (profiles)
        - Params: solar_potential[profile, t], efficiency[profile], capital_cost_per_kw[profile],
          om_per_kw_year[profile], existing_solar_capacity[node, profile], max_capacity_area (per-node scalar)
        - Vars: solar_capacity_adopted[node, profile], solar_generation[node, profile, t]
        - Constraints: generation[node,profile,t] <= (existing + adopted) * potential * efficiency (per node, profile, t);
                       at each node: sum over profiles of (existing+adopted)/efficiency <= max_capacity_area
        - objective_contribution: annual cost summed over nodes and profiles
        - electricity_supply_term[node, t]: sum over profiles of solar_generation[node, profile, t] for balance at that node
    """
    # ----- Time set -----
    if not hasattr(model, "T"):
        model.T = pyo.Set(initialize=range(len(data.indices["time"])), ordered=True)
    T = model.T

    # ----- Nodes: one per load (multi-node = multiple loads) -----
    load_keys = data.static.get("electricity_load_keys") or []
    if not load_keys:
        raise ValueError(
            "solar_pv block requires data.static['electricity_load_keys'] (load data first)"
        )
    NODES = list(load_keys)

    # ----- Solar profiles: one per key (fixed, 1-D tracking, etc.) -----
    solar_keys = data.static.get("solar_production_keys") or []
    if not solar_keys:
        raise ValueError(
            "solar_pv block requires data.static['solar_production_keys'] (load solar data first)"
        )
    SOLAR = list(solar_keys)
    production_by_profile = {
        key: list(data.timeseries[key]) for key in SOLAR
    }

    # ----- Resolve technical and financial parameters -----
    params = (data.tech_params.get("solar_pv") or {}).copy()
    for k, v in DEFAULT_SOLAR_PV_PARAMS.items():
        params.setdefault(k, v)

    # Per-profile params (efficiency, capital, O&M). Existing capacity is per (node, profile) only.
    efficiency_list, capital_list, om_list = _params_per_profile(SOLAR, params)

    max_area = params.get("max_capacity_area")
    max_capacity_area = float(max_area) if (max_area is not None and max_area >= 0) else 1e9

    # Existing capacity: 0 at every (node, profile) unless existing_solar_capacity_by_node_and_profile is set.
    # That way we never apply one number to every node; solar is added at each node only where you specify.
    existing_by_node_profile = params.get("existing_solar_capacity_by_node_and_profile") or {}
    existing_init = {}
    for n in NODES:
        for p in SOLAR:
            val = 0.0
            if isinstance(existing_by_node_profile.get(n), dict):
                val = float(existing_by_node_profile[n].get(p, 0.0))
            elif (n, p) in existing_by_node_profile:
                val = float(existing_by_node_profile[(n, p)])
            existing_init[(n, p)] = val

    # Capital amortization
    fin = data.static.get("financials") or {}
    amortization_factor = annualization_factor_debt_equity(**fin)

    # ----- Block rule -----
    def block_rule(b):
        # --- Sets ---
        b.NODES = pyo.Set(initialize=NODES, ordered=True)
        b.SOLAR = pyo.Set(initialize=SOLAR, ordered=True)

        # --- Parameters: potential (kWh/kW) per profile per time step (same at all nodes) ---
        b.solar_potential = pyo.Param(
            b.SOLAR,
            T,
            initialize={(p, t): production_by_profile[p][t] for p in SOLAR for t in T},
            within=pyo.NonNegativeReals,
        )
        # Per-profile technical/economic params
        b.efficiency = pyo.Param(
            b.SOLAR,
            initialize={p: efficiency_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.capital_cost_per_kw = pyo.Param(
            b.SOLAR,
            initialize={p: capital_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.om_per_kw_year = pyo.Param(
            b.SOLAR,
            initialize={p: om_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        # Per-node, per-profile existing capacity (kW)
        b.existing_solar_capacity = pyo.Param(
            b.NODES,
            b.SOLAR,
            initialize=existing_init,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.max_capacity_area = pyo.Param(
            initialize=max_capacity_area, within=pyo.NonNegativeReals, mutable=True
        )

        # --- Decision variables: capacity and generation per node, per profile ---
        b.solar_capacity_adopted = pyo.Var(b.NODES, b.SOLAR, within=pyo.NonNegativeReals)
        b.solar_generation = pyo.Var(b.NODES, b.SOLAR, T, within=pyo.NonNegativeReals)

        # --- Constraint: for each (node, profile, t), generation <= (existing + adopted) * potential * efficiency ---
        def generation_limits_rule(m, node, profile, t):
            return m.solar_generation[node, profile, t] <= (
                (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                * m.solar_potential[profile, t]
                * m.efficiency[profile]
            )

        b.generation_limits = pyo.Constraint(b.NODES, b.SOLAR, T, rule=generation_limits_rule)

        # --- Constraint: at each node, footprint limit — sum over profiles of (existing + adopted)/efficiency <= max_capacity_area ---
        def capacity_area_cap_rule(m, node):
            return sum(
                (m.existing_solar_capacity[node, p] + m.solar_capacity_adopted[node, p]) / m.efficiency[p]
                for p in m.SOLAR
            ) <= m.max_capacity_area

        b.capacity_area_cap = pyo.Constraint(b.NODES, rule=capacity_area_cap_rule)

        # --- Annual cost: summed over nodes and profiles ---
        b.objective_contribution = sum(
            b.capital_cost_per_kw[p] * b.solar_capacity_adopted[n, p] * amortization_factor
            + b.om_per_kw_year[p] * b.solar_capacity_adopted[n, p]
            for n in b.NODES for p in b.SOLAR
        )

        # --- Supply term per node for electricity balance: at each node, total solar generation at t ---
        # Balance at node n: load[n,t] = electricity_supply_term[n,t] + ...
        b.electricity_supply_term = pyo.Expression(
            b.NODES,
            T,
            rule=lambda m, n, t: sum(m.solar_generation[n, p, t] for p in m.SOLAR),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv


def register(model: Any, data: Any) -> pyo.Block | None:
    """
    Attach the Solar PV block if solar data is present; otherwise do nothing.

    Returns the block if attached, None otherwise.
    """
    if not data.static.get("solar_production_keys"):
        return None
    return add_solar_pv_block(model, data)
