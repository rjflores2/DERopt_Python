"""Diesel generator technology block with selectable formulations.

Diesel is modeled per node and per time step and contributes electricity as a
dispatchable source. A single technology package supports multiple internal
formulations selected by a parameter.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import (
    FORMULATION_DIESEL_BINARY,
    FORMULATION_DIESEL_LP,
    FORMULATION_DIESEL_UNIT_MILP,
    resolve_diesel_generator_block_inputs,
)


def add_diesel_generator_block(
    model: Any,
    data: Any,
    *,
    diesel_generator_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Diesel Generator block (one node per load bus, one time index
    per optimization period). Generation is in kW (or kWh per period for 1-hour steps).

    1. Data and other inputs
       - ``data.static["electricity_load_keys"]`` -> ordered node keys (already loaded on ``model.NODES``)
       - ``model.T`` -> time periods from ``model.core``
       - ``data.timeseries[node]`` -> used for ``diesel_binary`` peak load ``maximum_load_at_node`` (same length as ``model.T``)
       - ``diesel_generator_params`` -> user options merged with defaults (fuel may be set as ``fuel_cost_per_gallon``
         plus heating-value conversions in ``technologies/diesel_generator/inputs.py``; only ``fuel_cost_per_kwh_diesel``
         appears on the Pyomo block)
       - ``financials`` -> used to annualize adopted capital cost

    2. Sets (Pyomo ``Set``)
       - ``model.T`` -> time index used by the diesel block
       - ``model.NODES`` -> node index used by the diesel block

    3. Variables (Pyomo ``Var``)
       - Shared: ``diesel_generation[node, t]`` (continuous nonnegative)
       - ``diesel_lp``:
         - ``diesel_capacity_adopted[node]`` (continuous nonnegative, when adoption enabled)
       - ``diesel_binary``:
         - ``diesel_capacity_adopted[node]`` (continuous nonnegative, when adoption enabled)
         - ``diesel_on[node, t]`` (binary on/off per timestep)
       - ``diesel_unit_milp``:
         - ``diesel_units_adopted[node]`` (integer nonnegative, when adoption enabled)
         - ``diesel_units_on[node, t]`` (integer nonnegative)

    4. Parameters (Pyomo ``Param``)
       - ``capital_cost_per_kw`` / ``capital_cost_per_unit``
       - ``fixed_om_per_kw_year`` / ``fixed_om_per_unit_year``
       - ``variable_om_per_kwh``
       - ``fuel_cost_per_kwh_diesel`` ($/kWh diesel fuel energy) / ``electric_efficiency`` (variable fuel cost uses
         ``fuel_cost_per_kwh_diesel / electric_efficiency`` per kWh electricity generated)
       - ``minimum_loading_fraction``
       - ``unit_capacity_kw``
       - ``existing_capacity[node]`` (continuous-capacity formulations)
       - ``existing_unit_count[node]`` (discrete-units formulation)
       - ``capacity_adoption_limit[node]`` / ``unit_adoption_limit[node]``
       - ``amortization_factor``

    5. Other named components on the block
       - ``installed_capacity[node]`` -> continuous installed kW for ``diesel_lp`` and ``diesel_binary``
       - ``maximum_load_at_node[node]`` (``diesel_binary`` only) -> peak ``data.timeseries`` load over ``model.T``;
         commitment big-M and min-load relaxation (may cap output below nameplate if peak load < installed capacity)
       - ``installed_unit_count[node]`` -> total installed units for ``diesel_unit_milp``
       - ``maximum_total_units[node]`` -> bound for discrete unit-count logic

    6. Contribution to electricity sources
       - ``electricity_source_term[node, t]`` -> diesel generation

    7. Contribution to the cost function
       - ``diesel_capital_costs`` -> annualized adopted-capacity or adopted-unit capital cost
       - ``diesel_fixed_operations_and_maintenance`` -> adopted asset fixed O&M
       - ``diesel_variable_operating_costs`` -> variable O&M + fuel cost on generation
       - ``objective_contribution`` -> sum of the three expressions above
       - ``cost_non_optimizing_annual`` -> existing-asset fixed O&M (reporting only)

    8. Constraints
       - Shared:
         - generation nonnegative (via variable domain)
       - ``diesel_lp``:
         - ``generation_limits``: generation <= installed continuous capacity
       - ``diesel_binary``:
         - ``generation_capacity_limit``: generation <= installed continuous capacity
         - ``generation_commitment_big_m``: generation <= maximum_load_at_node * diesel_on
         - ``generation_min_loading``: minimum loading when ``diesel_on = 1``; when off, relaxed with
           ``maximum_load_at_node * (1 - diesel_on)``
       - ``diesel_unit_milp``:
         - ``units_on_limit``: units on <= installed units
         - ``generation_upper_by_units``: generation <= unit_capacity * units_on
         - ``generation_lower_by_units``: generation >= minimum_loading * unit_capacity * units_on
    """
    T = model.T
    nodes = list(model.NODES)
    resolved = resolve_diesel_generator_block_inputs(
        diesel_generator_params=diesel_generator_params,
        financials=financials,
        nodes=nodes,
    )

    formulation = resolved.formulation
    allow_adoption = resolved.allow_adoption

    T_list = list(T)
    if formulation == FORMULATION_DIESEL_BINARY:
        max_load_by_node = {
            node: max(float(data.timeseries[node][t]) for t in T_list)
            for node in nodes
        }
    else:
        max_load_by_node = {}

    def block_rule(diesel_block):
        #Diesel generator capital cost per kW
        diesel_block.capital_cost_per_kw = pyo.Param(
            initialize=resolved.capital_cost_per_kw, within=pyo.NonNegativeReals, mutable=True
        )
        #Diesel generator capital cost per unit
        diesel_block.capital_cost_per_unit = pyo.Param(
            initialize=resolved.capital_cost_per_unit, within=pyo.NonNegativeReals, mutable=True
        )
        #Diesel generator fixed O&M per kW per year
        diesel_block.fixed_om_per_kw_year = pyo.Param(
            initialize=resolved.fixed_om_per_kw_year, within=pyo.NonNegativeReals, mutable=True
        )
        #Diesel generator fixed O&M per unit per year
        diesel_block.fixed_om_per_unit_year = pyo.Param(
            initialize=resolved.fixed_om_per_unit_year, within=pyo.NonNegativeReals, mutable=True
        )
        #Diesel generator variable O&M per kWh
        diesel_block.variable_om_per_kwh = pyo.Param(
            initialize=resolved.variable_om_per_kwh, within=pyo.NonNegativeReals, mutable=True
        )
        # $/kWh diesel fuel energy; gallon/BTU conversion is done in inputs.py only.
        diesel_block.fuel_cost_per_kwh_diesel = pyo.Param(
            initialize=resolved.fuel_cost_per_kwh_diesel, within=pyo.NonNegativeReals, mutable=True
        )
        diesel_block.electric_efficiency = pyo.Param(
            initialize=resolved.electric_efficiency, within=pyo.NonNegativeReals, mutable=True
        )
        # Diesel generator minimum part load fraction - fraction of nameplate capacity where feasible operation is possible
        diesel_block.minimum_loading_fraction = pyo.Param(
            initialize=resolved.minimum_loading_fraction, within=pyo.NonNegativeReals, mutable=True
        )
        # For MILP model, the capacity of a single diesel generator
        diesel_block.unit_capacity_kw = pyo.Param(
            initialize=resolved.unit_capacity_kw, within=pyo.NonNegativeReals, mutable=True
        )
        # Any existing capacity on the node
        diesel_block.existing_capacity = pyo.Param(
            nodes, initialize=resolved.existing_capacity_by_node, within=pyo.NonNegativeReals, mutable=True
        )
        # Any existing unit count on the node
        diesel_block.existing_unit_count = pyo.Param(
            nodes, initialize=resolved.existing_unit_count_by_node, within=pyo.NonNegativeIntegers, mutable=True
        )
        # Maximum additional capacity that can be adopted on the node
        diesel_block.capacity_adoption_limit = pyo.Param(
            nodes, initialize=resolved.capacity_adoption_limit_by_node, within=pyo.NonNegativeReals, mutable=True
        )
        # Maximum additional unit count that can be adopted on the node
        diesel_block.unit_adoption_limit = pyo.Param(
            nodes, initialize=resolved.unit_adoption_limit_by_node, within=pyo.NonNegativeIntegers, mutable=True
        )
        # Amortization factor for capital cost
        diesel_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor, within=pyo.NonNegativeReals, mutable=True
        )
        # Electrical output from a diesel generator - Used in all diesel formulations
        diesel_block.diesel_generation = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if formulation in (FORMULATION_DIESEL_LP, FORMULATION_DIESEL_BINARY):
            if allow_adoption:
                # Diesel generation capacity varaibles for new diesel generation
                diesel_block.diesel_capacity_adopted = pyo.Var(nodes, within=pyo.NonNegativeReals)
                # Expresion for installed and total capacity
                def installed_capacity_rule(m, node):
                    return m.existing_capacity[node] + m.diesel_capacity_adopted[node]
            else:
                def installed_capacity_rule(m, node):
                    return m.existing_capacity[node]

            diesel_block.installed_capacity = pyo.Expression(nodes, rule=installed_capacity_rule)

            if formulation == FORMULATION_DIESEL_LP:
                def generation_limits_rule(m, node, t):
                    return m.diesel_generation[node, t] <= m.installed_capacity[node]

                diesel_block.generation_limits = pyo.Constraint(nodes, T, rule=generation_limits_rule)
            else:
                # Peak load over the horizon (data.timeseries). Used as M in diesel_on linearization.
                # If other sinks can draw power beyond contemporaneous load, peak load may under-estimate max diesel output.
                diesel_block.maximum_load_at_node = pyo.Param(
                    nodes, initialize=max_load_by_node, within=pyo.NonNegativeReals, mutable=True
                )
                diesel_block.diesel_on = pyo.Var(nodes, T, within=pyo.Binary)

                def generation_capacity_limit_rule(m, node, t):
                    return m.diesel_generation[node, t] <= m.installed_capacity[node]

                def generation_commitment_big_m_rule(m, node, t):
                    return m.diesel_generation[node, t] <= m.maximum_load_at_node[node] * m.diesel_on[node, t]

                def generation_min_loading_rule(m, node, t):
                    return m.diesel_generation[node, t] >= (
                        m.minimum_loading_fraction * m.installed_capacity[node]
                        - m.maximum_load_at_node[node] * (1 - m.diesel_on[node, t])
                    )

                diesel_block.generation_capacity_limit = pyo.Constraint(
                    nodes, T, rule=generation_capacity_limit_rule
                )
                diesel_block.generation_commitment_big_m = pyo.Constraint(
                    nodes, T, rule=generation_commitment_big_m_rule
                )
                diesel_block.generation_min_loading = pyo.Constraint(
                    nodes, T, rule=generation_min_loading_rule
                )

            if allow_adoption:
                diesel_block.diesel_capital_costs = pyo.Expression(
                    expr=sum(
                        diesel_block.capital_cost_per_kw
                        * diesel_block.diesel_capacity_adopted[node]
                        * diesel_block.amortization_factor
                        for node in nodes
                    )
                )
                diesel_block.diesel_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(
                        diesel_block.fixed_om_per_kw_year * diesel_block.diesel_capacity_adopted[node]
                        for node in nodes
                    )
                )
            else:
                diesel_block.diesel_capital_costs = pyo.Expression(expr=0.0)
                diesel_block.diesel_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            diesel_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    diesel_block.fixed_om_per_kw_year * diesel_block.existing_capacity[node]
                    for node in nodes
                )
            )

        else:
            if allow_adoption:
                diesel_block.diesel_units_adopted = pyo.Var(nodes, within=pyo.NonNegativeIntegers)

                def installed_unit_count_rule(m, node):
                    return m.existing_unit_count[node] + m.diesel_units_adopted[node]
            else:
                def installed_unit_count_rule(m, node):
                    return m.existing_unit_count[node]

            diesel_block.installed_unit_count = pyo.Expression(nodes, rule=installed_unit_count_rule)
            diesel_block.maximum_total_units = pyo.Expression(
                nodes, rule=lambda m, node: m.existing_unit_count[node] + m.unit_adoption_limit[node]
            )
            diesel_block.diesel_units_on = pyo.Var(nodes, T, within=pyo.NonNegativeIntegers)

            def units_on_limit_rule(m, node, t):
                return m.diesel_units_on[node, t] <= m.installed_unit_count[node]

            def generation_upper_by_units_rule(m, node, t):
                return m.diesel_generation[node, t] <= m.unit_capacity_kw * m.diesel_units_on[node, t]

            def generation_lower_by_units_rule(m, node, t):
                return m.diesel_generation[node, t] >= (
                    m.minimum_loading_fraction * m.unit_capacity_kw * m.diesel_units_on[node, t]
                )

            diesel_block.units_on_limit = pyo.Constraint(nodes, T, rule=units_on_limit_rule)
            diesel_block.generation_upper_by_units = pyo.Constraint(
                nodes, T, rule=generation_upper_by_units_rule
            )
            diesel_block.generation_lower_by_units = pyo.Constraint(
                nodes, T, rule=generation_lower_by_units_rule
            )

            if allow_adoption:
                diesel_block.diesel_capital_costs = pyo.Expression(
                    expr=sum(
                        diesel_block.capital_cost_per_unit
                        * diesel_block.diesel_units_adopted[node]
                        * diesel_block.amortization_factor
                        for node in nodes
                    )
                )
                diesel_block.diesel_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(
                        diesel_block.fixed_om_per_unit_year * diesel_block.diesel_units_adopted[node]
                        for node in nodes
                    )
                )
            else:
                diesel_block.diesel_capital_costs = pyo.Expression(expr=0.0)
                diesel_block.diesel_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            diesel_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    diesel_block.fixed_om_per_unit_year * diesel_block.existing_unit_count[node]
                    for node in nodes
                )
            )

        diesel_block.diesel_variable_operating_costs = pyo.Expression(
            expr=sum(
                (
                    diesel_block.variable_om_per_kwh
                    + diesel_block.fuel_cost_per_kwh_diesel / diesel_block.electric_efficiency
                )
                * diesel_block.diesel_generation[node, t]
                for node in nodes
                for t in T
            )
        )
        diesel_block.objective_contribution = pyo.Expression(
            expr=(
                diesel_block.diesel_capital_costs
                + diesel_block.diesel_fixed_operations_and_maintenance
                + diesel_block.diesel_variable_operating_costs
            )
        )
        diesel_block.electricity_source_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.diesel_generation[node, t]
        )

    model.diesel_generator = pyo.Block(rule=block_rule)
    return model.diesel_generator
