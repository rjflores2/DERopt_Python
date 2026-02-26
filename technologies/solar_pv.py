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
# capital cost, and O&M. Set solar_pv_params (typically from case config)
# and add "params_by_profile" in one of two ways:
#
# Option A — By profile key (use the same keys as in data.static["solar_production_keys"]):
#
#   solar_pv_params = {
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
#   solar_pv_params = {
#       "max_capacity_area": 10_000,
#       "params_by_profile": [
#           {"efficiency": 0.20, "capital_cost_per_kw": 1500, "om_per_kw_year": 18},   # fixed
#           {"efficiency": 0.22, "capital_cost_per_kw": 2100, "om_per_kw_year": 24},   # 1-D tracking
#       ],
#   }
#
# You can override only some fields per profile; the rest come from defaults or
# from top-level solar_pv_params (e.g. "max_capacity_area" applies to all).
#
# Existing capacity is NOT set per-profile here. It is per (node, profile). Use
# existing_solar_capacity_by_node_and_profile in solar_pv_params to specify
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
    has_area_limit: bool
    max_capacity_area: float | None
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

    max_area = params.get("max_capacity_area")
    has_area_limit = max_area is not None and max_area >= 0
    max_capacity_area = float(max_area) if has_area_limit else None

    existing_init = _resolve_existing_capacity(nodes, solar, params)

    fin = financials or {}
    amortization_factor = annualization_factor_debt_equity(**fin)

    return _ResolvedSolarInputs(
        efficiency_list=efficiency_list,
        capital_list=capital_list,
        om_list=om_list,
        existing_init=existing_init,
        has_area_limit=has_area_limit,
        max_capacity_area=max_capacity_area,
        amortization_factor=amortization_factor,
    )


# -----------------------------------------------------------------------------
# Block builder
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
          om_per_kw_year[profile], existing_solar_capacity[node, profile]; max_capacity_area only if set
        - Vars: solar_capacity_adopted[node, profile], solar_generation[node, profile, t]
        - Constraints: generation[node,profile,t] <= (existing + adopted) * potential * efficiency;
                       if max_capacity_area is set: at each node, sum over profiles of (existing+adopted)/efficiency <= max_capacity_area
        - objective_contribution: annual cost summed over nodes and profiles
        - electricity_supply_term[node, t]: sum over profiles of solar_generation[node, profile, t] for balance at that node
    """
    # ----- Indices and time series from data -----
    T = model.T
    NODES = list(model.NODES)  # model.NODES is created in core from electricity_load_keys

    solar_keys = data.static.get("solar_production_keys") or [] # Get the solar production keys, which are data labels in the data structure for each individual solar technology
    if not solar_keys: # If there are no solar production keys, raise an error - THERE HAS TO BE A SOLAR TECHNOLOGY! OBVI!!
        raise ValueError("solar_pv block requires data.static['solar_production_keys'] (load solar data first)")
    SOLAR = list(solar_keys)
    production_by_profile = {key: list(data.timeseries[key]) for key in SOLAR}

    # ----- Resolved technology parameters (defaults + overrides) -----
    r = _resolve_solar_block_inputs(solar_pv_params, financials, NODES, SOLAR) # Resolve the technology parameters for the solar PV technology

    # ----- Block rule -----
    # Use model.NODES (from core) for node indexing; only SOLAR is block-local.
    _nodes = model.NODES

    def block_rule(b):
        b.SOLAR = pyo.Set(initialize=SOLAR, ordered=True)

        b.solar_potential = pyo.Param(
            b.SOLAR, T,
            initialize={(p, t): production_by_profile[p][t] for p in SOLAR for t in T},
            within=pyo.NonNegativeReals,
        )
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
        if r.has_area_limit:
            b.max_capacity_area = pyo.Param(
                initialize=r.max_capacity_area, within=pyo.NonNegativeReals, mutable=True
            )

        b.solar_capacity_adopted = pyo.Var(_nodes, b.SOLAR, within=pyo.NonNegativeReals)
        b.solar_generation = pyo.Var(_nodes, b.SOLAR, T, within=pyo.NonNegativeReals)

        def generation_limits_rule(m, node, profile, t):
            return m.solar_generation[node, profile, t] <= (
                (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                * m.solar_potential[profile, t] * m.efficiency[profile]
            )
        b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule)

        if r.has_area_limit:
            def capacity_area_cap_rule(m, node):
                return sum(
                    (m.existing_solar_capacity[node, p] + m.solar_capacity_adopted[node, p]) / m.efficiency[p]
                    for p in m.SOLAR
                ) <= m.max_capacity_area
            b.capacity_area_cap = pyo.Constraint(_nodes, rule=capacity_area_cap_rule)

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
