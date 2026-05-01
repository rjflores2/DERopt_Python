"""Wind turbine technology block."""

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
    """Attach wind turbine generation to the model."""
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
                wind_profile: resolved.capital_list[profile_idx]
                for profile_idx, wind_profile in enumerate(wind_profiles)
            },
            within=pyo.Reals,
            mutable=True,
        )
        wind_block.om_per_kw_year = pyo.Param(
            wind_block.WIND,
            initialize={
                wind_profile: resolved.om_list[profile_idx]
                for profile_idx, wind_profile in enumerate(wind_profiles)
            },
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
                + resolved.existing_cap_recovery_per_kw[profile_idx]
                * wind_block.existing_wind_capacity[node, wind_profile]
                for profile_idx, wind_profile in enumerate(wind_block.WIND)
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
