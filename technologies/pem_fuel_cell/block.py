"""PEM fuel cell technology block with selectable LP, binary, or unit MILP formulations.

Hydrogen is modeled on a **lower heating value (LHV)** basis in **kWh-H2_LHV** per timestep.
**HHV is not used**. Electrical output is **kWh-electric per timestep**.

Binary formulation: electricity generation is tied to ``fuel_cell_on`` with a **big-M equal to the
node's peak electricity load (kWh per timestep)** over the modeled horizon (from
``data.timeseries``). That matches the diesel-generator style linearization: when the cell is on,
output is capped by contemporaneous-scale demand; when off, generation is forced to zero. If the
fuel cell can export beyond local load, increase installed capacity limits or use ``pem_fuel_cell_lp`` / ``pem_fuel_cell_unit_milp``.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import (
    FORMULATION_PEM_FUEL_CELL_BINARY,
    FORMULATION_PEM_FUEL_CELL_LP,
    resolve_pem_fuel_cell_block_inputs,
)


def add_pem_fuel_cell_block(
    model: Any,
    data: Any,
    *,
    pem_fuel_cell_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the PEM fuel cell block (one node per load bus, one time index per period).

    1. Data and other inputs
       - ``model.NODES``, ``model.T``
       - ``data.static["time_step_hours"]`` (defaults to 1.0)
       - ``data.timeseries`` — peak load per node for binary big-M

    2. Variables
       - ``electricity_generation_kwh_electric[node, t]`` (nonnegative)
       - ``pem_fuel_cell_lp`` / ``pem_fuel_cell_binary`` with adoption: ``capacity_adopted_kw[node]``
       - ``pem_fuel_cell_binary``: ``fuel_cell_on[node, t]`` (binary)
       - ``pem_fuel_cell_unit_milp``: ``units_adopted[node]``, ``units_on[node, t]`` (integers when adoption on)

    3. Parameters / expressions
       - ``hydrogen_lhv_to_electric_efficiency`` — kWh-electric per kWh-H2_LHV
       - ``hydrogen_consumption_kwh_h2_lhv[node, t]`` = generation / efficiency
       - ``maximum_load_kwh_per_timestep[node]`` (binary) — big-M for commitment

    4. Balance contributions
       - ``electricity_source_term`` = generation
       - ``hydrogen_sink_term`` = hydrogen consumption (LHV energy basis)

    5. Objective
       - Capital + fixed O&M on adopted assets + variable O&M on electricity output
       - ``cost_non_optimizing_annual`` on existing capacity/units
    """
    T = model.T
    nodes = list(model.NODES)
    dt_hours = float((getattr(data, "static", {}) or {}).get("time_step_hours") or 1.0)

    resolved = resolve_pem_fuel_cell_block_inputs(
        pem_fuel_cell_params,
        time_step_hours=dt_hours,
        financials=financials,
        nodes=nodes,
    )
    formulation = resolved.formulation
    allow_adoption = resolved.allow_adoption
    T_list = list(T)

    max_load_kwh_by_node: dict[str, float] = {}
    if formulation == FORMULATION_PEM_FUEL_CELL_BINARY:
        max_load_kwh_by_node = {
            node: max(float(data.timeseries[node][t]) for t in T_list) for node in nodes
        }

    def block_rule(fc_block):
        fc_block.time_step_hours = pyo.Param(
            initialize=resolved.time_step_hours, within=pyo.PositiveReals, mutable=True
        )
        fc_block.capital_cost_per_kw = pyo.Param(
            initialize=resolved.capital_cost_per_kw, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.capital_cost_per_unit = pyo.Param(
            initialize=resolved.capital_cost_per_unit, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.fixed_om_per_kw_year = pyo.Param(
            initialize=resolved.fixed_om_per_kw_year, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.fixed_om_per_unit_year = pyo.Param(
            initialize=resolved.fixed_om_per_unit_year, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.variable_om_per_kwh_electric = pyo.Param(
            initialize=resolved.variable_om_per_kwh_electric, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.hydrogen_lhv_to_electric_efficiency = pyo.Param(
            initialize=resolved.hydrogen_lhv_to_electric_efficiency,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        fc_block.minimum_loading_fraction = pyo.Param(
            initialize=resolved.minimum_loading_fraction, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.unit_capacity_kw = pyo.Param(
            initialize=resolved.unit_capacity_kw, within=pyo.NonNegativeReals, mutable=True
        )
        fc_block.existing_capacity_kw = pyo.Param(
            nodes,
            initialize=resolved.existing_capacity_kw_by_node,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        fc_block.existing_unit_count = pyo.Param(
            nodes,
            initialize=resolved.existing_unit_count_by_node,
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        fc_block.capacity_adoption_limit_kw = pyo.Param(
            nodes,
            initialize=resolved.capacity_adoption_limit_kw_by_node,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        fc_block.unit_adoption_limit = pyo.Param(
            nodes,
            initialize=resolved.unit_adoption_limit_by_node,
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        fc_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor, within=pyo.NonNegativeReals, mutable=True
        )

        fc_block.electricity_generation_kwh_electric = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if formulation in (FORMULATION_PEM_FUEL_CELL_LP, FORMULATION_PEM_FUEL_CELL_BINARY):
            if allow_adoption:
                fc_block.capacity_adopted_kw = pyo.Var(nodes, within=pyo.NonNegativeReals)

                def installed_kw_rule(m, node):
                    return m.existing_capacity_kw[node] + m.capacity_adopted_kw[node]

                def adoption_cap_rule(m, node):
                    return m.capacity_adopted_kw[node] <= m.capacity_adoption_limit_kw[node]

                fc_block.adoption_capacity_limit = pyo.Constraint(nodes, rule=adoption_cap_rule)
            else:

                def installed_kw_rule(m, node):
                    return m.existing_capacity_kw[node]

            fc_block.installed_electric_capacity_kw = pyo.Expression(nodes, rule=installed_kw_rule)

            def max_energy_per_timestep_rule(m, node):
                return m.installed_electric_capacity_kw[node] * m.time_step_hours

            fc_block.max_electricity_kwh_electric_per_timestep = pyo.Expression(
                nodes, rule=max_energy_per_timestep_rule
            )

            def generation_capacity_rule(m, node, t):
                return m.electricity_generation_kwh_electric[node, t] <= m.max_electricity_kwh_electric_per_timestep[
                    node
                ]

            fc_block.generation_capacity_limit = pyo.Constraint(nodes, T, rule=generation_capacity_rule)

            if formulation == FORMULATION_PEM_FUEL_CELL_BINARY:
                fc_block.maximum_load_kwh_per_timestep = pyo.Param(
                    nodes, initialize=max_load_kwh_by_node, within=pyo.NonNegativeReals, mutable=True
                )
                fc_block.fuel_cell_on = pyo.Var(nodes, T, within=pyo.Binary)

                def generation_big_m_rule(m, node, t):
                    return m.electricity_generation_kwh_electric[node, t] <= m.maximum_load_kwh_per_timestep[
                        node
                    ] * m.fuel_cell_on[node, t]

                def generation_min_load_rule(m, node, t):
                    return m.electricity_generation_kwh_electric[node, t] >= (
                        m.minimum_loading_fraction * m.max_electricity_kwh_electric_per_timestep[node]
                        - m.maximum_load_kwh_per_timestep[node] * (1 - m.fuel_cell_on[node, t])
                    )

                fc_block.generation_commitment_big_m = pyo.Constraint(nodes, T, rule=generation_big_m_rule)
                fc_block.generation_min_loading = pyo.Constraint(nodes, T, rule=generation_min_load_rule)

            if allow_adoption:
                fc_block.pem_fc_capital_costs = pyo.Expression(
                    expr=sum(
                        fc_block.capital_cost_per_kw
                        * fc_block.capacity_adopted_kw[node]
                        * fc_block.amortization_factor
                        for node in nodes
                    )
                )
                fc_block.pem_fc_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(fc_block.fixed_om_per_kw_year * fc_block.capacity_adopted_kw[node] for node in nodes)
                )
            else:
                fc_block.pem_fc_capital_costs = pyo.Expression(expr=0.0)
                fc_block.pem_fc_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            fc_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(fc_block.fixed_om_per_kw_year * fc_block.existing_capacity_kw[node] for node in nodes)
            )

        else:  # unit_milp
            if allow_adoption:
                fc_block.units_adopted = pyo.Var(nodes, within=pyo.NonNegativeIntegers)

                def installed_units_rule(m, node):
                    return m.existing_unit_count[node] + m.units_adopted[node]

                def adoption_units_rule(m, node):
                    return m.units_adopted[node] <= m.unit_adoption_limit[node]

                fc_block.adoption_units_limit = pyo.Constraint(nodes, rule=adoption_units_rule)
            else:

                def installed_units_rule(m, node):
                    return m.existing_unit_count[node]

            fc_block.installed_unit_count = pyo.Expression(nodes, rule=installed_units_rule)
            fc_block.maximum_total_units = pyo.Expression(
                nodes,
                rule=lambda m, node: m.existing_unit_count[node] + m.unit_adoption_limit[node],
            )
            fc_block.units_on = pyo.Var(nodes, T, within=pyo.NonNegativeIntegers)

            def units_on_limit_rule(m, node, t):
                return m.units_on[node, t] <= m.installed_unit_count[node]

            def generation_upper_units_rule(m, node, t):
                cap_e = m.unit_capacity_kw * m.time_step_hours
                return m.electricity_generation_kwh_electric[node, t] <= cap_e * m.units_on[node, t]

            def generation_lower_units_rule(m, node, t):
                cap_e = m.unit_capacity_kw * m.time_step_hours
                return m.electricity_generation_kwh_electric[node, t] >= (
                    m.minimum_loading_fraction * cap_e * m.units_on[node, t]
                )

            fc_block.units_on_limit = pyo.Constraint(nodes, T, rule=units_on_limit_rule)
            fc_block.generation_upper_by_units = pyo.Constraint(nodes, T, rule=generation_upper_units_rule)
            fc_block.generation_lower_by_units = pyo.Constraint(nodes, T, rule=generation_lower_units_rule)

            if allow_adoption:
                fc_block.pem_fc_capital_costs = pyo.Expression(
                    expr=sum(
                        fc_block.capital_cost_per_unit
                        * fc_block.units_adopted[node]
                        * fc_block.amortization_factor
                        for node in nodes
                    )
                )
                fc_block.pem_fc_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(fc_block.fixed_om_per_unit_year * fc_block.units_adopted[node] for node in nodes)
                )
            else:
                fc_block.pem_fc_capital_costs = pyo.Expression(expr=0.0)
                fc_block.pem_fc_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            fc_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(fc_block.fixed_om_per_unit_year * fc_block.existing_unit_count[node] for node in nodes)
            )

        fc_block.hydrogen_consumption_kwh_h2_lhv = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.electricity_generation_kwh_electric[node, t]
            / m.hydrogen_lhv_to_electric_efficiency,
        )

        fc_block.pem_fc_variable_operating_costs = pyo.Expression(
            expr=sum(
                fc_block.variable_om_per_kwh_electric * fc_block.electricity_generation_kwh_electric[node, t]
                for node in nodes
                for t in T
            )
        )
        fc_block.objective_contribution = pyo.Expression(
            expr=(
                fc_block.pem_fc_capital_costs
                + fc_block.pem_fc_fixed_operations_and_maintenance
                + fc_block.pem_fc_variable_operating_costs
            )
        )
        fc_block.electricity_source_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.electricity_generation_kwh_electric[node, t]
        )
        fc_block.hydrogen_sink_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.hydrogen_consumption_kwh_h2_lhv[node, t]
        )

    model.pem_fuel_cell = pyo.Block(rule=block_rule)
    return model.pem_fuel_cell
