"""PEM electrolyzer technology block with selectable LP, binary, or unit MILP formulations.

Hydrogen is modeled on a **lower heating value (LHV)** basis in **kWh-H2_LHV** per timestep.
**HHV is not used** in this block. Electrical consumption is **kWh-electric per timestep**.

Binary formulation big-M (electricity consumption upper bound when on):
``maximum_load_kwh_per_timestep[node] * electrolyzer_binary_big_m_load_multiplier``. Peak site
electric load (kWh per timestep) is a conservative scale for *contemporaneous* demand; the
multiplier (default 15) is **much larger** so the linearization still allows electrolyzers to
draw power well above typical load when charging from **oversized on-site renewables** or
similar. Document and tune ``electrolyzer_binary_big_m_load_multiplier`` for your case.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import (
    FORMULATION_PEM_ELECTROLYZER_BINARY,
    FORMULATION_PEM_ELECTROLYZER_LP,
    resolve_pem_electrolyzer_block_inputs,
)


def add_pem_electrolyzer_block(
    model: Any,
    data: Any,
    *,
    pem_electrolyzer_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the PEM electrolyzer block (one node per load bus, one time index per period).

    1. Data and other inputs
       - ``data.static["electricity_load_keys"]`` / ``model.NODES``
       - ``model.T`` from ``model.core``
       - ``data.static["time_step_hours"]`` (defaults to 1.0 if missing) — converts nameplate kW to max kWh per timestep
       - ``data.timeseries[node]`` — peak load per node for binary big-M (same length as ``model.T``)
       - ``pem_electrolyzer_params`` / ``financials``

    2. Sets
       - ``model.T``, ``model.NODES``

    3. Variables
       - Shared: ``electricity_consumption_kwh_electric[node, t]`` (nonnegative)
       - ``pem_electrolyzer_lp`` / ``pem_electrolyzer_binary`` (with adoption): ``capacity_adopted_kw[node]`` (continuous)
       - ``pem_electrolyzer_binary``: ``electrolyzer_on[node, t]`` (binary)
       - ``pem_electrolyzer_unit_milp`` (with adoption): ``units_adopted[node]`` (integer); ``units_on[node, t]`` (integer)

    4. Pyomo parameters
       - Efficiency, costs, limits, ``time_step_hours``, ``electrolyzer_binary_big_m_load_multiplier`` (binary only)
       - ``maximum_load_kwh_per_timestep[node]`` (binary only) — max ``data.timeseries`` over the horizon
       - ``big_m_electricity_kwh_per_timestep[node]`` (binary only) = peak load × multiplier

    5. Named expressions
       - ``hydrogen_production_kwh_h2_lhv[node, t]`` = efficiency × electricity consumption (LHV basis)
       - ``installed_electric_capacity_kw[node]`` (``pem_electrolyzer_lp``/``pem_electrolyzer_binary``) or unit-based installed capacity (``pem_electrolyzer_unit_milp``)

    6. Balance contributions
       - ``electricity_sink_term[node, t]`` = electricity consumption
       - ``hydrogen_source_term[node, t]`` = hydrogen production (kWh-H2_LHV)

    7. Objective
       - Capital + fixed O&M (adopted assets) + variable O&M on electricity consumption
       - ``cost_non_optimizing_annual`` — fixed O&M on existing capacity/units only

    8. Formulation-specific constraints
       - ``pem_electrolyzer_lp``: consumption ≤ installed kW × Δt; production = η × consumption (via expressions)
       - ``pem_electrolyzer_binary``: big-M on consumption when on; min-load relaxation using ``big_m_electricity_kwh_per_timestep``
       - ``pem_electrolyzer_unit_milp``: integer units on; consumption bounds per unit capacity × Δt
    """
    T = model.T
    nodes = list(model.NODES)
    dt_hours = float((getattr(data, "static", {}) or {}).get("time_step_hours") or 1.0)

    resolved = resolve_pem_electrolyzer_block_inputs(
        pem_electrolyzer_params,
        time_step_hours=dt_hours,
        financials=financials,
        nodes=nodes,
    )
    formulation = resolved.formulation
    allow_adoption = resolved.allow_adoption
    T_list = list(T)

    max_load_kwh_by_node: dict[str, float] = {}
    big_m_elec_by_node: dict[str, float] = {}
    if formulation == FORMULATION_PEM_ELECTROLYZER_BINARY:
        max_load_kwh_by_node = {
            node: max(float(data.timeseries[node][t]) for t in T_list) for node in nodes
        }
        mult = resolved.electrolyzer_binary_big_m_load_multiplier
        big_m_elec_by_node = {node: max_load_kwh_by_node[node] * mult for node in nodes}

    def block_rule(ele_block):
        ele_block.time_step_hours = pyo.Param(
            initialize=resolved.time_step_hours, within=pyo.PositiveReals, mutable=True
        )
        ele_block.capital_cost_per_kw = pyo.Param(
            initialize=resolved.capital_cost_per_kw, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.capital_cost_per_unit = pyo.Param(
            initialize=resolved.capital_cost_per_unit, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.fixed_om_per_kw_year = pyo.Param(
            initialize=resolved.fixed_om_per_kw_year, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.fixed_om_per_unit_year = pyo.Param(
            initialize=resolved.fixed_om_per_unit_year, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.variable_om_per_kwh_electric = pyo.Param(
            initialize=resolved.variable_om_per_kwh_electric, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.electric_to_hydrogen_lhv_efficiency = pyo.Param(
            initialize=resolved.electric_to_hydrogen_lhv_efficiency,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        ele_block.minimum_loading_fraction = pyo.Param(
            initialize=resolved.minimum_loading_fraction, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.unit_capacity_kw = pyo.Param(
            initialize=resolved.unit_capacity_kw, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.existing_capacity_kw = pyo.Param(
            nodes,
            initialize=resolved.existing_capacity_kw_by_node,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        ele_block.existing_unit_count = pyo.Param(
            nodes,
            initialize=resolved.existing_unit_count_by_node,
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        ele_block.capacity_adoption_limit_kw = pyo.Param(
            nodes,
            initialize=resolved.capacity_adoption_limit_kw_by_node,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        ele_block.unit_adoption_limit = pyo.Param(
            nodes,
            initialize=resolved.unit_adoption_limit_by_node,
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        ele_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor, within=pyo.NonNegativeReals, mutable=True
        )
        ele_block.electrolyzer_binary_big_m_load_multiplier = pyo.Param(
            initialize=resolved.electrolyzer_binary_big_m_load_multiplier,
            within=pyo.PositiveReals,
            mutable=True,
        )

        ele_block.electricity_consumption_kwh_electric = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if formulation in (FORMULATION_PEM_ELECTROLYZER_LP, FORMULATION_PEM_ELECTROLYZER_BINARY):
            if allow_adoption:
                ele_block.capacity_adopted_kw = pyo.Var(nodes, within=pyo.NonNegativeReals)

                def installed_kw_rule(m, node):
                    return m.existing_capacity_kw[node] + m.capacity_adopted_kw[node]

                def adoption_cap_rule(m, node):
                    return m.capacity_adopted_kw[node] <= m.capacity_adoption_limit_kw[node]

                ele_block.adoption_capacity_limit = pyo.Constraint(nodes, rule=adoption_cap_rule)
            else:

                def installed_kw_rule(m, node):
                    return m.existing_capacity_kw[node]

            ele_block.installed_electric_capacity_kw = pyo.Expression(nodes, rule=installed_kw_rule)

            def max_energy_per_timestep_rule(m, node):
                return m.installed_electric_capacity_kw[node] * m.time_step_hours

            ele_block.max_electricity_kwh_electric_per_timestep = pyo.Expression(
                nodes, rule=max_energy_per_timestep_rule
            )

            def consumption_capacity_rule(m, node, t):
                return m.electricity_consumption_kwh_electric[node, t] <= m.max_electricity_kwh_electric_per_timestep[
                    node
                ]

            ele_block.consumption_capacity_limit = pyo.Constraint(nodes, T, rule=consumption_capacity_rule)

            if formulation == FORMULATION_PEM_ELECTROLYZER_BINARY:
                ele_block.maximum_load_kwh_per_timestep = pyo.Param(
                    nodes, initialize=max_load_kwh_by_node, within=pyo.NonNegativeReals, mutable=True
                )
                ele_block.big_m_electricity_kwh_per_timestep = pyo.Param(
                    nodes, initialize=big_m_elec_by_node, within=pyo.NonNegativeReals, mutable=True
                )
                ele_block.electrolyzer_on = pyo.Var(nodes, T, within=pyo.Binary)

                def consumption_big_m_rule(m, node, t):
                    return m.electricity_consumption_kwh_electric[node, t] <= m.big_m_electricity_kwh_per_timestep[
                        node
                    ] * m.electrolyzer_on[node, t]

                def consumption_min_load_rule(m, node, t):
                    return m.electricity_consumption_kwh_electric[node, t] >= (
                        m.minimum_loading_fraction * m.max_electricity_kwh_electric_per_timestep[node]
                        - m.big_m_electricity_kwh_per_timestep[node] * (1 - m.electrolyzer_on[node, t])
                    )

                ele_block.consumption_commitment_big_m = pyo.Constraint(nodes, T, rule=consumption_big_m_rule)
                ele_block.consumption_min_loading = pyo.Constraint(nodes, T, rule=consumption_min_load_rule)

            if allow_adoption:
                ele_block.pem_capital_costs = pyo.Expression(
                    expr=sum(
                        ele_block.capital_cost_per_kw
                        * ele_block.capacity_adopted_kw[node]
                        * ele_block.amortization_factor
                        for node in nodes
                    )
                )
                ele_block.pem_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(ele_block.fixed_om_per_kw_year * ele_block.capacity_adopted_kw[node] for node in nodes)
                )
            else:
                ele_block.pem_capital_costs = pyo.Expression(expr=0.0)
                ele_block.pem_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            ele_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(ele_block.fixed_om_per_kw_year * ele_block.existing_capacity_kw[node] for node in nodes)
            )

        else:  # unit_milp
            if allow_adoption:
                ele_block.units_adopted = pyo.Var(nodes, within=pyo.NonNegativeIntegers)

                def installed_units_rule(m, node):
                    return m.existing_unit_count[node] + m.units_adopted[node]

                def adoption_units_rule(m, node):
                    return m.units_adopted[node] <= m.unit_adoption_limit[node]

                ele_block.adoption_units_limit = pyo.Constraint(nodes, rule=adoption_units_rule)
            else:

                def installed_units_rule(m, node):
                    return m.existing_unit_count[node]

            ele_block.installed_unit_count = pyo.Expression(nodes, rule=installed_units_rule)
            ele_block.maximum_total_units = pyo.Expression(
                nodes,
                rule=lambda m, node: m.existing_unit_count[node] + m.unit_adoption_limit[node],
            )
            ele_block.units_on = pyo.Var(nodes, T, within=pyo.NonNegativeIntegers)

            def units_on_limit_rule(m, node, t):
                return m.units_on[node, t] <= m.installed_unit_count[node]

            def consumption_upper_units_rule(m, node, t):
                cap_e = m.unit_capacity_kw * m.time_step_hours
                return m.electricity_consumption_kwh_electric[node, t] <= cap_e * m.units_on[node, t]

            def consumption_lower_units_rule(m, node, t):
                cap_e = m.unit_capacity_kw * m.time_step_hours
                return m.electricity_consumption_kwh_electric[node, t] >= (
                    m.minimum_loading_fraction * cap_e * m.units_on[node, t]
                )

            ele_block.units_on_limit = pyo.Constraint(nodes, T, rule=units_on_limit_rule)
            ele_block.consumption_upper_by_units = pyo.Constraint(nodes, T, rule=consumption_upper_units_rule)
            ele_block.consumption_lower_by_units = pyo.Constraint(nodes, T, rule=consumption_lower_units_rule)

            if allow_adoption:
                ele_block.pem_capital_costs = pyo.Expression(
                    expr=sum(
                        ele_block.capital_cost_per_unit
                        * ele_block.units_adopted[node]
                        * ele_block.amortization_factor
                        for node in nodes
                    )
                )
                ele_block.pem_fixed_operations_and_maintenance = pyo.Expression(
                    expr=sum(ele_block.fixed_om_per_unit_year * ele_block.units_adopted[node] for node in nodes)
                )
            else:
                ele_block.pem_capital_costs = pyo.Expression(expr=0.0)
                ele_block.pem_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)

            ele_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(ele_block.fixed_om_per_unit_year * ele_block.existing_unit_count[node] for node in nodes)
            )

        ele_block.hydrogen_production_kwh_h2_lhv = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.electric_to_hydrogen_lhv_efficiency
            * m.electricity_consumption_kwh_electric[node, t],
        )

        ele_block.pem_variable_operating_costs = pyo.Expression(
            expr=sum(
                ele_block.variable_om_per_kwh_electric * ele_block.electricity_consumption_kwh_electric[node, t]
                for node in nodes
                for t in T
            )
        )
        ele_block.objective_contribution = pyo.Expression(
            expr=(
                ele_block.pem_capital_costs
                + ele_block.pem_fixed_operations_and_maintenance
                + ele_block.pem_variable_operating_costs
            )
        )
        ele_block.electricity_sink_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.electricity_consumption_kwh_electric[node, t]
        )
        ele_block.hydrogen_source_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.hydrogen_production_kwh_h2_lhv[node, t]
        )

    model.pem_electrolyzer = pyo.Block(rule=block_rule)
    return model.pem_electrolyzer
