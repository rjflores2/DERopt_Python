"""
Flow battery energy storage technology block.

Modeled per node and per time step, parallel to ``battery_energy_storage``, with one structural
difference: **energy capacity (kWh) and charge/discharge power capacity (kW) are independent**
design quantities. Power limits are ``<= total_power_capacity[node]``, not C-rate multiples of
energy capacity (no ``max_*_power_per_kwh``).

The block contributes to the system electricity balance via:
- ``electricity_source_term[node, t]`` (discharge adds to sources),
- ``electricity_sink_term[node, t]`` (charge adds to sinks),
consistent with ``model.core``.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer
from shared.cost_helpers import (
    annualized_fixed_cost_by_node,
    attach_standard_cost_expressions,
)

from .inputs import resolve_flow_battery_block_inputs


def add_flow_battery_energy_storage_block(
    model: pyo.Block,
    data: DataContainer,
    *,
    flow_battery_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Flow Battery Energy Storage block (one node per load bus, one time index
    per optimization period). Storage power is in kW; state of charge in kWh.

    **Difference from standard battery:** energy and power capacities are sized separately; power
    is not derived from energy capacity via a fixed C-rate.

    1. Data and other inputs
       - ``data.static["electricity_load_keys"]`` -> ordered node keys (already on ``model.NODES``)
       - ``model.T`` -> time periods from ``model.core``
       - ``flow_battery_params`` -> user options merged with defaults
       - ``financials`` -> annualization factor for adopted capacity capital costs

    2. Sets (Pyomo ``Set``)
       - ``model.T`` -> time index
       - ``model.NODES`` -> node index

    3. Variables (Pyomo ``Var``)
       - ``state_of_charge[node, t]`` -> stored energy (kWh)
       - ``charge_power[node, t]`` -> energy drawn to charge during the timestep
         (**kWh per timestep**; name retained for continuity)
       - ``discharge_power[node, t]`` -> energy delivered during the timestep
         (**kWh per timestep**)
       - When adoption is enabled:
         - ``energy_capacity_adopted[node]`` -> incremental energy capacity (kWh)
         - ``power_capacity_adopted[node]`` -> incremental charge/discharge power limit (kW)

    4. Parameters (Pyomo ``Param``)
       - ``charge_efficiency`` / ``discharge_efficiency``
       - ``state_of_charge_retention`` -> per-timestep SOC retention
       - ``minimum_state_of_charge`` / ``maximum_state_of_charge`` -> fractions of total energy capacity
       - ``existing_energy_capacity[node]`` / ``existing_power_capacity[node]`` -> kWh and kW
       - ``energy_capital_cost_per_kwh`` -> $/kWh (annualized on adopted energy capacity)
       - ``power_capital_cost_per_kw`` -> $/kW (annualized on adopted power capacity)
       - ``energy_om_per_kwh_year`` / ``power_om_per_kw_year`` -> fixed O&M on adopted capacity
       - ``amortization_factor`` -> applied to both capital cost streams
       - ``initial_soc_fraction`` -> optional; only defined when provided in inputs

    5. Named expressions
       - ``total_energy_capacity[node]`` -> existing + adopted kWh (existing only if no adoption)
       - ``total_power_capacity[node]`` -> existing + adopted kW (existing only if no adoption)

    6. Contribution to electricity sources and sinks
       - ``electricity_source_term[node, t]`` -> ``discharge_power[node, t]``
       - ``electricity_sink_term[node, t]`` -> ``charge_power[node, t]``

    7. Contribution to cost and reporting
       - ``flow_battery_energy_capital_costs`` / ``flow_battery_power_capital_costs``
         (per-component sub-expressions for reporting)
       - ``flow_battery_energy_fixed_om`` / ``flow_battery_power_fixed_om`` (adopted assets)
       - Standardized via ``shared.cost_helpers``: ``annual_capital_costs`` (sum of energy+power),
         ``fixed_om_adopted`` (sum of energy+power), ``fixed_om_existing``,
         ``objective_contribution``, ``cost_non_optimizing_annual``

    8. Constraints
       - ``state_of_charge_minimum`` / ``state_of_charge_maximum`` vs total energy capacity
       - ``charge_power_limit`` / ``discharge_power_limit`` -> ``flow <= total_power_capacity_kw
         * time_step_hours`` (kWh/timestep bound from the kW nameplate)
       - ``energy_balance`` -> SOC dynamics (same algebraic form as standard battery); since
         charge/discharge are already kWh/timestep, no dt multiplier is needed
       - ``initial_soc`` -> optional first-period SOC anchor
    """
    T = model.T
    nodes = list(model.NODES)
    horizon = list(T)
    time_index = {t: i for i, t in enumerate(horizon)}

    allow_adoption = (flow_battery_params or {}).get("allow_adoption", True)
    resolved = resolve_flow_battery_block_inputs(
        flow_battery_params=flow_battery_params,
        financials=financials,
        nodes=nodes,
    )

    def block_rule(fb_block):
        fb_block.charge_efficiency = pyo.Param(
            initialize=resolved.charge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.discharge_efficiency = pyo.Param(
            initialize=resolved.discharge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.state_of_charge_retention = pyo.Param(
            initialize=resolved.state_of_charge_retention,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.minimum_state_of_charge = pyo.Param(
            initialize=resolved.minimum_state_of_charge,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.maximum_state_of_charge = pyo.Param(
            initialize=resolved.maximum_state_of_charge,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.existing_energy_capacity = pyo.Param(
            nodes,
            initialize=resolved.existing_energy_capacity,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.existing_power_capacity = pyo.Param(
            nodes,
            initialize=resolved.existing_power_capacity,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        fb_block.energy_capital_cost_per_kwh = pyo.Param(
            initialize=resolved.energy_capital_cost_per_kwh,
            within=pyo.Reals,
            mutable=True,
        )
        fb_block.power_capital_cost_per_kw = pyo.Param(
            initialize=resolved.power_capital_cost_per_kw,
            within=pyo.Reals,
            mutable=True,
        )
        fb_block.energy_om_per_kwh_year = pyo.Param(
            initialize=resolved.energy_om_per_kwh_year,
            within=pyo.Reals,
            mutable=True,
        )
        fb_block.power_om_per_kw_year = pyo.Param(
            initialize=resolved.power_om_per_kw_year,
            within=pyo.Reals,
            mutable=True,
        )
        fb_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        if resolved.initial_soc_fraction is not None:
            fb_block.initial_soc_fraction = pyo.Param(
                initialize=resolved.initial_soc_fraction,
                within=pyo.NonNegativeReals,
                mutable=False,
            )

        fb_block.state_of_charge = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        fb_block.charge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        fb_block.discharge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            fb_block.energy_capacity_adopted = pyo.Var(nodes, within=pyo.NonNegativeReals)
            fb_block.power_capacity_adopted = pyo.Var(nodes, within=pyo.NonNegativeReals)

            def total_energy_capacity(m, node):
                return m.existing_energy_capacity[node] + m.energy_capacity_adopted[node]

            def total_power_capacity(m, node):
                return m.existing_power_capacity[node] + m.power_capacity_adopted[node]
        else:

            def total_energy_capacity(m, node):
                return m.existing_energy_capacity[node]

            def total_power_capacity(m, node):
                return m.existing_power_capacity[node]

        fb_block.total_energy_capacity = pyo.Expression(nodes, rule=total_energy_capacity)
        fb_block.total_power_capacity = pyo.Expression(nodes, rule=total_power_capacity)

        def state_of_charge_minimum_rule(m, node, t):
            return (
                m.state_of_charge[node, t]
                >= m.minimum_state_of_charge * m.total_energy_capacity[node]
            )

        def state_of_charge_maximum_rule(m, node, t):
            return (
                m.state_of_charge[node, t]
                <= m.maximum_state_of_charge * m.total_energy_capacity[node]
            )

        fb_block.state_of_charge_minimum = pyo.Constraint(
            nodes, T, rule=state_of_charge_minimum_rule
        )
        fb_block.state_of_charge_maximum = pyo.Constraint(
            nodes, T, rule=state_of_charge_maximum_rule
        )

        # Power capacity is kW nameplate; multiply by ``time_step_hours`` to bound the
        # kWh-per-timestep flow variable.
        def charge_power_limit_rule(m, node, t):
            return m.charge_power[node, t] <= (
                m.total_power_capacity[node] * model.time_step_hours
            )

        def discharge_power_limit_rule(m, node, t):
            return m.discharge_power[node, t] <= (
                m.total_power_capacity[node] * model.time_step_hours
            )

        fb_block.charge_power_limit = pyo.Constraint(nodes, T, rule=charge_power_limit_rule)
        fb_block.discharge_power_limit = pyo.Constraint(nodes, T, rule=discharge_power_limit_rule)

        def flow_battery_energy_balance_rule(m, node, t):
            time_step_index = time_index[t]
            previous_time_step = horizon[-1] if time_step_index == 0 else horizon[time_step_index - 1]
            return m.state_of_charge[node, t] == (
                m.state_of_charge_retention * m.state_of_charge[node, previous_time_step]
                + m.charge_efficiency * m.charge_power[node, t]
                - (1.0 / m.discharge_efficiency) * m.discharge_power[node, t]
            )

        fb_block.energy_balance = pyo.Constraint(nodes, T, rule=flow_battery_energy_balance_rule)

        if hasattr(fb_block, "initial_soc_fraction") and horizon:
            first_time_step = horizon[0]

            def initial_soc_rule(m, node):
                return m.state_of_charge[node, first_time_step] == (
                    m.initial_soc_fraction * m.total_energy_capacity[node]
                )

            fb_block.initial_soc = pyo.Constraint(nodes, rule=initial_soc_rule)

        fb_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.discharge_power[node, t],
        )
        fb_block.electricity_sink_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.charge_power[node, t],
        )

        annualized_capital_if_adopted = None
        fixed_om_adopted_if_adopted = None
        if allow_adoption:
            fb_block.flow_battery_energy_capital_costs = annualized_fixed_cost_by_node(
                cost_per_unit=fb_block.energy_capital_cost_per_kwh,
                capacity_var=fb_block.energy_capacity_adopted,
                nodes=nodes,
                amortization_factor=fb_block.amortization_factor,
            )
            fb_block.flow_battery_power_capital_costs = annualized_fixed_cost_by_node(
                cost_per_unit=fb_block.power_capital_cost_per_kw,
                capacity_var=fb_block.power_capacity_adopted,
                nodes=nodes,
                amortization_factor=fb_block.amortization_factor,
            )
            fb_block.flow_battery_energy_fixed_om = annualized_fixed_cost_by_node(
                cost_per_unit=fb_block.energy_om_per_kwh_year,
                capacity_var=fb_block.energy_capacity_adopted,
                nodes=nodes,
            )
            fb_block.flow_battery_power_fixed_om = annualized_fixed_cost_by_node(
                cost_per_unit=fb_block.power_om_per_kw_year,
                capacity_var=fb_block.power_capacity_adopted,
                nodes=nodes,
            )
            annualized_capital_if_adopted = pyo.Expression(
                expr=fb_block.flow_battery_energy_capital_costs
                + fb_block.flow_battery_power_capital_costs
            )
            fixed_om_adopted_if_adopted = pyo.Expression(
                expr=fb_block.flow_battery_energy_fixed_om
                + fb_block.flow_battery_power_fixed_om
            )

        fixed_om_existing = pyo.Expression(
            expr=sum(
                fb_block.energy_om_per_kwh_year * fb_block.existing_energy_capacity[node]
                + fb_block.power_om_per_kw_year * fb_block.existing_power_capacity[node]
                for node in nodes
            )
        )

        attach_standard_cost_expressions(
            fb_block,
            allow_adoption=allow_adoption,
            fixed_om_existing=fixed_om_existing,
            annualized_capital_if_adopted=annualized_capital_if_adopted,
            fixed_om_adopted_if_adopted=fixed_om_adopted_if_adopted,
        )

    model.flow_battery_energy_storage = pyo.Block(rule=block_rule)
    return model.flow_battery_energy_storage
