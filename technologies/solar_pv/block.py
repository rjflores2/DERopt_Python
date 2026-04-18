"""
Solar PV technology block.

Solar is modeled per node and per profile:
- Nodes: one per load (data.static["electricity_load_keys"]). Multi-node cases have
  multiple nodes; single-node has one. Each node can host its own solar capacity.
- Profiles: fixed, 1-D tracking, etc. (data.static["solar_production_keys"]). Each
  profile has its own capacity and generation at each node.

Decision variables: solar_capacity_adopted[node, profile], solar_generation[node, profile, t].
Example: 3 nodes x 2 technologies = 6 capacity variables, each with its own constraints.

Solar potential is indexed per (node, profile, t). By default each node uses the profile's
canonical time series (data.timeseries[profile_key]) — correct for co-located nodes /
single-site microgrids where all nodes share the same irradiance. For geographically
separated nodes (different latitude / microclimate), users can assign a distinct
data.timeseries key per (node, profile) via solar_resource_assignment_by_node_and_profile
in the solar_pv params. Missing pairs fall back to the default broadcast, so existing
single-site configs see no change.

Keep this block simple and well commented; technology components are a main place
for manual programming and must stay transparent.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer
from shared.cost_helpers import (
    annualized_fixed_cost_by_node_category,
    attach_standard_cost_expressions,
)

from .inputs import resolve_solar_block_inputs


def add_solar_pv_block(
    model: pyo.Block,
    data: DataContainer,
    *,
    solar_pv_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Solar PV block (one node index per load bus, one solar profile index
    per solar resource column). The same solar potential time series is used at every node.

    1. Data and other inputs
       - ``data.static["solar_production_keys"]`` -> ordered Python list of solar profile keys
       - ``data.timeseries[profile_key]`` -> solar potential timeseries data (kWh/kW installed capacity)
       - ``solar_pv_params`` -> user-supplied Solar PV parameters, merged with defaults
       - ``financials`` -> financial inputs used to annualize capital costs

    2. Sets (Pyomo ``Set``)
       - ``model.T`` -> time index used by the Solar PV block
       - ``model.NODES`` -> node index used by the Solar PV block
       - ``solar_block.SOLAR`` -> solar profile index for the Solar PV block
       - ``solar_block.AREA_LIMIT_INDEX`` -> index of ``(node, profile)`` pairs where an area limit is defined

    3. Variables (Pyomo ``Var``)
       - ``solar_generation[node, solar_profile, t]`` -> kWh generated in period ``t``
       - ``solar_capacity_adopted[node, solar_profile]`` -> additional kW to install if adoption is allowed

    4. Parameters (Pyomo ``Param``)
       - ``solar_potential[node, solar_profile, t]`` -> solar potential from ``data.timeseries``;
         defaults to the profile's canonical series broadcast to every node, overridable per
         (node, profile) via ``solar_resource_assignment_by_node_and_profile``
       - ``efficiency[solar_profile]`` -> Solar PV system efficiency
       - ``capital_cost_per_kw[solar_profile]`` -> Solar PV capital cost ($/kW installed)
       - ``om_per_kw_year[solar_profile]`` -> Solar PV fixed O&M cost ($/kW-year)
       - ``existing_solar_capacity[node, solar_profile]`` -> existing solar capacity at each node (kW)
       - ``max_capacity_area[node, solar_profile]`` -> max allowable solar PV area on indexed pairs

    5. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - sum of ``solar_generation[node, solar_profile, t]`` across all solar profiles

    6. Contribution to the cost function - ``objective_contribution``
       - adopted solar capacity -> annualized capital on adopted kW plus fixed O&M on adopted kW
       - existing solar capacity -> fixed O&M plus optional existing-capital recovery on existing kW

    7. Constraints
       - ``generation_limits`` -> generation limited by installed capacity and solar potential
       - ``capacity_area_cap`` -> optional area cap where configured
    """
    T = model.T
    nodes = list(model.NODES)

    solar_profiles = list(data.static.get("solar_production_keys") or [])
    if not solar_profiles:
        raise ValueError("solar_pv block requires data.static['solar_production_keys'] (load solar data first)")
    production_by_profile = {key: list(data.timeseries[key]) for key in solar_profiles}

    allow_adoption = (solar_pv_params or {}).get("allow_adoption", True)
    resolved = resolve_solar_block_inputs(
        solar_pv_params=solar_pv_params,
        financials=financials,
        nodes=nodes,
        solar_profiles=solar_profiles,
    )

    # Build per-(node, profile) time series. Default: broadcast the profile's canonical
    # series. Override: pull an alternate series from data.timeseries for any (node, profile)
    # pair listed in the resource-assignment input. Missing pairs fall through to default.
    time_horizon_len = len(list(T))
    resource_assignment = resolved.resource_assignment_by_node_profile
    solar_potential_init: dict[tuple[str, str, int], float] = {}
    for node in nodes:
        for solar_profile in solar_profiles:
            resource_key = resource_assignment.get((node, solar_profile), solar_profile)
            if resource_key not in data.timeseries:
                raise ValueError(
                    f"solar_pv: resource key {resource_key!r} for (node={node!r}, profile={solar_profile!r}) "
                    f"not found in data.timeseries. Load the resource file into the container first, "
                    f"or use a key that exists."
                )
            series = list(data.timeseries[resource_key])
            if len(series) < time_horizon_len:
                raise ValueError(
                    f"solar_pv: resource series {resource_key!r} has {len(series)} values but model "
                    f"horizon T has {time_horizon_len}. Re-align the source data before loading."
                )
            for t in T:
                solar_potential_init[(node, solar_profile, t)] = series[t]

    def block_rule(solar_block):
        solar_block.SOLAR = pyo.Set(initialize=solar_profiles, ordered=True)

        solar_block.solar_potential = pyo.Param(
            model.NODES,
            solar_block.SOLAR,
            T,
            initialize=solar_potential_init,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        solar_block.efficiency = pyo.Param(
            solar_block.SOLAR,
            initialize={
                solar_profile: resolved.efficiency_list[profile_idx]
                for profile_idx, solar_profile in enumerate(solar_profiles)
            },
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        solar_block.capital_cost_per_kw = pyo.Param(
            solar_block.SOLAR,
            initialize={
                solar_profile: resolved.capital_list[profile_idx]
                for profile_idx, solar_profile in enumerate(solar_profiles)
            },
            within=pyo.Reals,
            mutable=True,
        )
        solar_block.om_per_kw_year = pyo.Param(
            solar_block.SOLAR,
            initialize={
                solar_profile: resolved.om_list[profile_idx]
                for profile_idx, solar_profile in enumerate(solar_profiles)
            },
            within=pyo.Reals,
            mutable=True,
        )
        solar_block.existing_solar_capacity = pyo.Param(
            nodes,
            solar_block.SOLAR,
            initialize=resolved.existing_init,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        if resolved.has_area_limits:
            solar_block.AREA_LIMIT_INDEX = pyo.Set(dimen=2, initialize=resolved.area_index, ordered=True)
            solar_block.max_capacity_area = pyo.Param(
                solar_block.AREA_LIMIT_INDEX,
                initialize=resolved.max_capacity_area_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=False,
            )

        solar_block.solar_generation = pyo.Var(nodes, solar_block.SOLAR, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            solar_block.solar_capacity_adopted = pyo.Var(nodes, solar_block.SOLAR, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    * m.solar_potential[node, profile, t]
                )

            solar_block.generation_limits = pyo.Constraint(nodes, solar_block.SOLAR, T, rule=generation_limits_rule)

            if resolved.has_area_limits:
                def capacity_area_cap_rule(m, node, profile):
                    return (
                        (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                        / m.efficiency[profile]
                    ) <= m.max_capacity_area[node, profile]

                solar_block.capacity_area_cap = pyo.Constraint(
                    solar_block.AREA_LIMIT_INDEX, rule=capacity_area_cap_rule
                )

            annualized_capital_if_adopted = annualized_fixed_cost_by_node_category(
                cost_per_unit_by_category=solar_block.capital_cost_per_kw,
                capacity_var_by_node_category=solar_block.solar_capacity_adopted,
                nodes=nodes,
                categories=solar_block.SOLAR,
                amortization_factor=resolved.amortization_factor,
            )
            fixed_om_adopted_if_adopted = annualized_fixed_cost_by_node_category(
                cost_per_unit_by_category=solar_block.om_per_kw_year,
                capacity_var_by_node_category=solar_block.solar_capacity_adopted,
                nodes=nodes,
                categories=solar_block.SOLAR,
            )
        else:
            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    m.existing_solar_capacity[node, profile] * m.solar_potential[node, profile, t]
                )

            solar_block.generation_limits = pyo.Constraint(
                nodes, solar_block.SOLAR, T, rule=generation_limits_rule_existing_only
            )
            annualized_capital_if_adopted = None
            fixed_om_adopted_if_adopted = None

        # Existing-asset annual cost includes O&M plus residual capital recovery on
        # already-installed capacity (per-profile recovery factor from inputs).
        fixed_om_existing = pyo.Expression(
            expr=sum(
                solar_block.om_per_kw_year[solar_profile]
                * solar_block.existing_solar_capacity[node, solar_profile]
                + resolved.existing_cap_recovery_per_kw[profile_idx]
                * solar_block.existing_solar_capacity[node, solar_profile]
                for profile_idx, solar_profile in enumerate(solar_block.SOLAR)
                for node in nodes
            )
        )

        attach_standard_cost_expressions(
            solar_block,
            allow_adoption=allow_adoption,
            fixed_om_existing=fixed_om_existing,
            annualized_capital_if_adopted=annualized_capital_if_adopted,
            fixed_om_adopted_if_adopted=fixed_om_adopted_if_adopted,
        )

        solar_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: sum(
                m.solar_generation[node, solar_profile, t] for solar_profile in m.SOLAR
            ),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv
