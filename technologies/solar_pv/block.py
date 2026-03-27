"""
Solar PV technology block.

Solar is modeled per node and per profile:
- Nodes: one per load (data.static["electricity_load_keys"]). Multi-node cases have
  multiple nodes; single-node has one. Each node can host its own solar capacity.
- Profiles: fixed, 1-D tracking, etc. (data.static["solar_production_keys"]). Each
  profile has its own capacity and generation at each node.

Decision variables: solar_capacity_adopted[node, profile], solar_generation[node, profile, t].
Example: 3 nodes x 2 technologies = 6 capacity variables, each with its own constraints.
Solar potential is the same profile time series at every node (no per-node irradiance yet).

Keep this block simple and well commented; technology components are a main place
for manual programming and must stay transparent.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import resolve_solar_block_inputs


def add_solar_pv_block(
    model: Any,
    data: Any,
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
       - ``solar_potential[solar_profile, t]`` -> solar potential from ``data.timeseries``
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

    def block_rule(solar_block):
        solar_block.SOLAR = pyo.Set(initialize=solar_profiles, ordered=True)

        solar_block.solar_potential = pyo.Param(
            solar_block.SOLAR,
            T,
            initialize={
                (solar_profile, t): production_by_profile[solar_profile][t]
                for solar_profile in solar_profiles
                for t in T
            },
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        solar_block.efficiency = pyo.Param(
            solar_block.SOLAR,
            initialize={
                solar_profile: resolved.efficiency_list[profile_idx]
                for profile_idx, solar_profile in enumerate(solar_profiles)
            },
            within=pyo.NonNegativeReals,
            mutable=True,
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
            mutable=True,
        )
        if resolved.has_area_limits:
            solar_block.AREA_LIMIT_INDEX = pyo.Set(dimen=2, initialize=resolved.area_index, ordered=True)
            solar_block.max_capacity_area = pyo.Param(
                solar_block.AREA_LIMIT_INDEX,
                initialize=resolved.max_capacity_area_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=True,
            )

        solar_block.solar_generation = pyo.Var(nodes, solar_block.SOLAR, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            solar_block.solar_capacity_adopted = pyo.Var(nodes, solar_block.SOLAR, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    * m.solar_potential[profile, t]
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

            solar_block.solar_capital_costs = pyo.Expression(
                expr=sum(
                    solar_block.capital_cost_per_kw[solar_profile]
                    * solar_block.solar_capacity_adopted[node, solar_profile]
                    * resolved.amortization_factor
                    for solar_profile in solar_block.SOLAR
                    for node in nodes
                )
            )
            solar_block.solar_fixed_operations_and_maintenance = pyo.Expression(
                expr=sum(
                    solar_block.om_per_kw_year[solar_profile] * solar_block.solar_capacity_adopted[node, solar_profile]
                    for solar_profile in solar_block.SOLAR
                    for node in nodes
                )
            )
            solar_block.objective_contribution = pyo.Expression(
                expr=solar_block.solar_capital_costs + solar_block.solar_fixed_operations_and_maintenance
            )
            solar_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    solar_block.om_per_kw_year[solar_profile] * solar_block.existing_solar_capacity[node, solar_profile]
                    + resolved.existing_cap_recovery_per_kw[profile_idx]
                    * solar_block.existing_solar_capacity[node, solar_profile]
                    for profile_idx, solar_profile in enumerate(solar_block.SOLAR)
                    for node in nodes
                )
            )
        else:
            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    m.existing_solar_capacity[node, profile] * m.solar_potential[profile, t]
                )

            solar_block.generation_limits = pyo.Constraint(
                nodes, solar_block.SOLAR, T, rule=generation_limits_rule_existing_only
            )
            solar_block.solar_capital_costs = pyo.Expression(expr=0.0)
            solar_block.solar_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)
            solar_block.objective_contribution = pyo.Expression(expr=0.0)
            solar_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    solar_block.om_per_kw_year[solar_profile] * solar_block.existing_solar_capacity[node, solar_profile]
                    + resolved.existing_cap_recovery_per_kw[profile_idx]
                    * solar_block.existing_solar_capacity[node, solar_profile]
                    for profile_idx, solar_profile in enumerate(solar_block.SOLAR)
                    for node in nodes
                )
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
