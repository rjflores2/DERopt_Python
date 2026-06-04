"""
Wind turbine technology block.

Wind is modeled per node and per profile:
- Nodes: one per load bus (data.static["electricity_load_keys"]). Each node can host
  its own wind capacity.
- Profiles: one per wind resource column in the input file (data.static["wind_production_keys"]).
  Multiple profiles allow different turbine types or sites to be compared within a single run.

Wind potential is indexed per (node, profile, t) but is node-invariant by design: all nodes
share the same wind resource time series. This is appropriate for small-scale, co-located
systems where spatial variation in wind is negligible.

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

from .inputs import resolve_wind_block_inputs


def add_wind_turbine_block(
    model: pyo.Block,
    data: DataContainer,
    *,
    wind_turbine_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the wind turbine block (one node index per load bus, one profile index
    per wind resource column).

    1. Data and other inputs
       - ``data.static["wind_production_keys"]`` -> ordered list of wind profile keys
       - ``data.timeseries[profile_key]`` -> wind potential time series (kWh/kW installed capacity)
       - ``wind_turbine_params`` -> user-supplied wind turbine parameters, merged with defaults
       - ``financials`` -> financial inputs used to annualize capital costs

    2. Sets (Pyomo ``Set``)
       - ``model.T`` -> time index used by the wind turbine block
       - ``model.NODES`` -> node index used by the wind turbine block
       - ``wind_block.WIND`` -> wind profile index
       - ``wind_block.CAPACITY_LIMIT_INDEX`` -> index of ``(node, profile)`` pairs where a capacity limit is defined

    3. Variables (Pyomo ``Var``)
       - ``wind_generation[node, wind_profile, t]`` -> kWh generated in period ``t``
       - ``wind_capacity_adopted[node, wind_profile]`` -> additional kW to install (only if adoption is allowed)

    4. Parameters (Pyomo ``Param``)
       - ``wind_potential[node, wind_profile, t]`` -> wind potential from ``data.timeseries`` (kWh/kW);
         node-invariant by design — all nodes share the same wind resource time series
       - ``capital_cost_per_kw[wind_profile]`` -> wind turbine capital cost ($/kW installed)
       - ``om_per_kw_year[wind_profile]`` -> wind turbine fixed O&M cost ($/kW-year)
       - ``existing_wind_capacity[node, wind_profile]`` -> pre-existing wind capacity at each node (kW)
       - ``max_capacity[node, wind_profile]`` -> maximum allowable installed capacity on indexed pairs (kW)

    5. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - sum of ``wind_generation[node, wind_profile, t]`` across all wind profiles

    6. Contribution to the cost function - ``objective_contribution``
       - adopted wind capacity -> annualized capital on adopted kW plus fixed O&M on adopted kW
       - existing wind capacity -> fixed O&M plus optional existing-capital recovery on existing kW

    7. Constraints
       - ``generation_limits`` -> generation limited by installed capacity and wind potential
       - ``capacity_cap`` -> optional capacity cap where configured
    """
    T = model.T
    nodes = list(model.NODES)

    wind_profiles = list(data.static.get("wind_production_keys") or [])
    if not wind_profiles:
        raise ValueError(
            "wind_turbine block requires data.static['wind_production_keys'] (load wind data first)"
        )
    production_by_profile = {key: list(data.timeseries[key]) for key in wind_profiles}

    allow_adoption = (wind_turbine_params or {}).get("allow_adoption", True)
    resolved = resolve_wind_block_inputs(
        wind_turbine_params=wind_turbine_params,
        financials=financials,
        nodes=nodes,
        wind_profiles=wind_profiles,
    )

    def block_rule(wind_block):
        wind_block.WIND = pyo.Set(initialize=wind_profiles, ordered=True)

        wind_block.wind_potential = pyo.Param(
            model.NODES,
            wind_block.WIND,
            T,
            initialize={
                (node, wind_profile, t): production_by_profile[wind_profile][t]
                for node in nodes
                for wind_profile in wind_profiles
                for t in T
            },
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        wind_block.capital_cost_per_kw = pyo.Param(
            wind_block.WIND,
            initialize={
                wind_profile: resolved.capital_list[wind_profile] for wind_profile in wind_profiles
            },
            within=pyo.Reals,
            mutable=True,
        )
        wind_block.om_per_kw_year = pyo.Param(
            wind_block.WIND,
            initialize={wind_profile: resolved.om_list[wind_profile] for wind_profile in wind_profiles},
            within=pyo.Reals,
            mutable=True,
        )
        wind_block.existing_wind_capacity = pyo.Param(
            nodes,
            wind_block.WIND,
            initialize=resolved.existing_init,
            within=pyo.NonNegativeReals,
            mutable=False,
        )

        if resolved.has_capacity_limits:
            wind_block.CAPACITY_LIMIT_INDEX = pyo.Set(
                dimen=2,
                initialize=resolved.capacity_limit_index,
                ordered=True,
            )
            wind_block.max_capacity = pyo.Param(
                wind_block.CAPACITY_LIMIT_INDEX,
                initialize=resolved.max_capacity_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=False,
            )

        wind_block.wind_generation = pyo.Var(nodes, wind_block.WIND, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            wind_block.wind_capacity_adopted = pyo.Var(nodes, wind_block.WIND, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t):
                return m.wind_generation[node, profile, t] <= (
                    (m.existing_wind_capacity[node, profile] + m.wind_capacity_adopted[node, profile])
                    * m.wind_potential[node, profile, t]
                )

            wind_block.generation_limits = pyo.Constraint(nodes, wind_block.WIND, T, rule=generation_limits_rule)

            if resolved.has_capacity_limits:

                def capacity_cap_rule(m, node, profile):
                    return (
                        m.existing_wind_capacity[node, profile] + m.wind_capacity_adopted[node, profile]
                    ) <= m.max_capacity[node, profile]

                wind_block.capacity_cap = pyo.Constraint(
                    wind_block.CAPACITY_LIMIT_INDEX, rule=capacity_cap_rule
                )

            annualized_capital_if_adopted = annualized_fixed_cost_by_node_category(
                cost_per_unit_by_category=wind_block.capital_cost_per_kw,
                capacity_var_by_node_category=wind_block.wind_capacity_adopted,
                nodes=nodes,
                categories=wind_block.WIND,
                amortization_factor=resolved.amortization_factor,
            )
            fixed_om_adopted_if_adopted = annualized_fixed_cost_by_node_category(
                cost_per_unit_by_category=wind_block.om_per_kw_year,
                capacity_var_by_node_category=wind_block.wind_capacity_adopted,
                nodes=nodes,
                categories=wind_block.WIND,
            )
        else:

            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.wind_generation[node, profile, t] <= (
                    m.existing_wind_capacity[node, profile] * m.wind_potential[node, profile, t]
                )

            wind_block.generation_limits = pyo.Constraint(
                nodes, wind_block.WIND, T, rule=generation_limits_rule_existing_only
            )
            annualized_capital_if_adopted = None
            fixed_om_adopted_if_adopted = None

        fixed_om_existing = pyo.Expression(
            expr=sum(
                wind_block.om_per_kw_year[wind_profile]
                * wind_block.existing_wind_capacity[node, wind_profile]
                + resolved.existing_cap_recovery_per_kw[wind_profile]
                * wind_block.existing_wind_capacity[node, wind_profile]
                for wind_profile in wind_block.WIND
                for node in nodes
            )
        )

        attach_standard_cost_expressions(
            wind_block,
            allow_adoption=allow_adoption,
            fixed_om_existing=fixed_om_existing,
            annualized_capital_if_adopted=annualized_capital_if_adopted,
            fixed_om_adopted_if_adopted=fixed_om_adopted_if_adopted,
        )

        wind_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: sum(
                m.wind_generation[node, wind_profile, t] for wind_profile in m.WIND
            ),
        )

    model.wind_turbine = pyo.Block(rule=block_rule)
    return model.wind_turbine
