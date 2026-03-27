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
       - ``b.SOLAR`` -> solar profile index for the Solar PV block
       - ``b.AREA_LIMIT_INDEX`` -> index of ``(node, profile)`` pairs where an area limit is defined

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

    def block_rule(b):
        b.SOLAR = pyo.Set(initialize=solar_profiles, ordered=True)

        b.solar_potential = pyo.Param(
            b.SOLAR,
            T,
            initialize={(p, t): production_by_profile[p][t] for p in solar_profiles for t in T},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.efficiency = pyo.Param(
            b.SOLAR,
            initialize={p: resolved.efficiency_list[i] for i, p in enumerate(solar_profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.capital_cost_per_kw = pyo.Param(
            b.SOLAR,
            initialize={p: resolved.capital_list[i] for i, p in enumerate(solar_profiles)},
            within=pyo.Reals,
            mutable=True,
        )
        b.om_per_kw_year = pyo.Param(
            b.SOLAR,
            initialize={p: resolved.om_list[i] for i, p in enumerate(solar_profiles)},
            within=pyo.Reals,
            mutable=True,
        )
        b.existing_solar_capacity = pyo.Param(
            nodes,
            b.SOLAR,
            initialize=resolved.existing_init,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        if resolved.has_area_limits:
            b.AREA_LIMIT_INDEX = pyo.Set(dimen=2, initialize=resolved.area_index, ordered=True)
            b.max_capacity_area = pyo.Param(
                b.AREA_LIMIT_INDEX,
                initialize=resolved.max_capacity_area_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=True,
            )

        b.solar_generation = pyo.Var(nodes, b.SOLAR, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            b.solar_capacity_adopted = pyo.Var(nodes, b.SOLAR, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    * m.solar_potential[profile, t]
                )

            b.generation_limits = pyo.Constraint(nodes, b.SOLAR, T, rule=generation_limits_rule)

            if resolved.has_area_limits:
                def capacity_area_cap_rule(m, node, profile):
                    return (
                        (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                        / m.efficiency[profile]
                    ) <= m.max_capacity_area[node, profile]

                b.capacity_area_cap = pyo.Constraint(b.AREA_LIMIT_INDEX, rule=capacity_area_cap_rule)

            b.solar_capital_costs = pyo.Expression(
                expr=sum(
                    b.capital_cost_per_kw[p] * b.solar_capacity_adopted[n, p] * resolved.amortization_factor
                    for p in b.SOLAR
                    for n in nodes
                )
            )
            b.solar_fixed_operations_and_maintenance = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.solar_capacity_adopted[n, p]
                    for p in b.SOLAR
                    for n in nodes
                )
            )
            b.objective_contribution = pyo.Expression(
                expr=b.solar_capital_costs + b.solar_fixed_operations_and_maintenance
            )
            b.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    + resolved.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                    for i, p in enumerate(b.SOLAR)
                    for n in nodes
                )
            )
        else:
            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    m.existing_solar_capacity[node, profile] * m.solar_potential[profile, t]
                )

            b.generation_limits = pyo.Constraint(nodes, b.SOLAR, T, rule=generation_limits_rule_existing_only)
            b.solar_capital_costs = pyo.Expression(expr=0.0)
            b.solar_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)
            b.objective_contribution = pyo.Expression(expr=0.0)
            b.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    + resolved.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                    for i, p in enumerate(b.SOLAR)
                    for n in nodes
                )
            )

        b.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, n, t: sum(m.solar_generation[n, p, t] for p in m.SOLAR),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv
