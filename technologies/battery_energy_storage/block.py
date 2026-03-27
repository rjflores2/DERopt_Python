"""
Battery energy storage technology block.

Battery is modeled per node and per time step with:
- State of charge (energy in storage) at each node and time.
- Separate charging and discharging power variables at each node and time.
- An energy balance that links state of charge across time steps.
- Power limits that scale with installed energy capacity (C-rates).
- An energy-capacity limit (existing + adopted capacity) at each node.

The block contributes to the system electricity balance via:
- electricity_source_term[node, t] (discharging adds to sources),
- electricity_sink_term[node, t]  (charging adds to sinks),
so it plugs directly into the electricity balance built in model.core.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import resolve_battery_block_inputs


def add_battery_energy_storage_block(
    model: Any,
    data: Any,
    *,
    battery_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Battery Energy Storage block (one node per load bus, one time index
    per optimization period). Storage power is in kW; state of charge in kWh.

    1. Data and other inputs
       - ``model.T`` -> time periods from ``model.core``
       - ``model.NODES`` -> node keys matching ``electricity_load_keys``
       - ``battery_params`` -> user options merged with defaults
       - ``financials`` -> used to annualize capital on adopted kWh capacity

    2. Parameters (Pyomo ``Param``)
       - ``capital_cost_per_kwh`` -> resolved $/kWh (one-time capital; annualized in objective)
       - ``om_per_kwh_year`` -> resolved fixed O&M ($/kWh-year)

    3. Variables (Pyomo ``Var``)
       - ``energy_state[node, t]`` -> state of charge (kWh)
       - ``charge_power[node, t]`` -> charging power (kW)
       - ``discharge_power[node, t]`` -> discharging power (kW)
       - ``energy_capacity_adopted[node]`` -> incremental storage energy capacity (kWh)

    4. Other named components on the block
       - ``total_energy_capacity[node]`` -> existing + adopted kWh, or existing only

    5. Contribution to electricity sources and sinks
       - ``electricity_source_term[node, t]`` -> discharge power
       - ``electricity_sink_term[node, t]`` -> charge power

    6. Contribution to the cost function
       - ``battery_capital_costs`` / ``battery_fixed_operations_and_maintenance`` (adopted kWh)
       - ``objective_contribution`` -> sum of those when adoption is enabled
       - reporting-only existing O&M in ``cost_non_optimizing_annual``

    7. Constraints
       - ``energy_capacity_limit`` -> ``energy_state <= total_energy_capacity``
       - ``charge_power_limit`` / ``discharge_power_limit`` -> power ``<=`` C-rate times capacity
       - ``energy_balance`` -> SOC update across the horizon
       - ``initial_soc`` -> optional first-period SOC anchor
    """
    T = model.T
    nodes = list(model.NODES)
    horizon = list(T)
    # O(1) predecessor lookup for energy balance (avoid horizon.index(t) in constraint rules).
    time_index = {t: i for i, t in enumerate(horizon)}

    allow_adoption = (battery_params or {}).get("allow_adoption", True)
    resolved = resolve_battery_block_inputs(
        battery_params=battery_params,
        financials=financials,
        nodes=nodes,
    )

    def block_rule(battery_block):
        battery_block.capital_cost_per_kwh = pyo.Param(
            initialize=resolved.capital_cost_per_kwh,
            within=pyo.Reals,
            mutable=True,
        )
        battery_block.om_per_kwh_year = pyo.Param(
            initialize=resolved.om_per_kwh_year,
            within=pyo.Reals,
            mutable=True,
        )

        battery_block.energy_state = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        battery_block.charge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        battery_block.discharge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            battery_block.energy_capacity_adopted = pyo.Var(nodes, within=pyo.NonNegativeReals)

            def total_energy_capacity(m, node):
                return resolved.existing_energy_capacity[node] + m.energy_capacity_adopted[node]
        else:
            def total_energy_capacity(m, node):
                return resolved.existing_energy_capacity[node]

        battery_block.total_energy_capacity = pyo.Expression(nodes, rule=total_energy_capacity)

        def energy_capacity_limit_rule(m, node, t):
            return m.energy_state[node, t] <= m.total_energy_capacity[node]

        battery_block.energy_capacity_limit = pyo.Constraint(nodes, T, rule=energy_capacity_limit_rule)

        def charge_power_limit_rule(m, node, t):
            return m.charge_power[node, t] <= (
                resolved.max_charge_power_per_kwh * m.total_energy_capacity[node]
            )

        def discharge_power_limit_rule(m, node, t):
            return m.discharge_power[node, t] <= (
                resolved.max_discharge_power_per_kwh * m.total_energy_capacity[node]
            )

        battery_block.charge_power_limit = pyo.Constraint(nodes, T, rule=charge_power_limit_rule)
        battery_block.discharge_power_limit = pyo.Constraint(nodes, T, rule=discharge_power_limit_rule)

        def energy_balance_rule(m, node, t):
            time_step_index = time_index[t]
            previous_time_step = horizon[-1] if time_step_index == 0 else horizon[time_step_index - 1]
            return m.energy_state[node, t] == (
                m.energy_state[node, previous_time_step]
                + resolved.charge_efficiency * m.charge_power[node, t]
                - (1.0 / resolved.discharge_efficiency) * m.discharge_power[node, t]
            )

        battery_block.energy_balance = pyo.Constraint(nodes, T, rule=energy_balance_rule)

        if resolved.initial_soc_fraction is not None and horizon:
            first_time_step = horizon[0]

            def initial_soc_rule(m, node):
                return m.energy_state[node, first_time_step] == (
                    resolved.initial_soc_fraction * m.total_energy_capacity[node]
                )

            battery_block.initial_soc = pyo.Constraint(nodes, rule=initial_soc_rule)

        battery_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.discharge_power[node, t],
        )
        battery_block.electricity_sink_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.charge_power[node, t],
        )

        if allow_adoption:
            battery_block.battery_capital_costs = pyo.Expression(
                expr=sum(
                    battery_block.capital_cost_per_kwh
                    * battery_block.energy_capacity_adopted[node]
                    * resolved.amortization_factor
                    for node in nodes
                )
            )
            battery_block.battery_fixed_operations_and_maintenance = pyo.Expression(
                expr=sum(
                    battery_block.om_per_kwh_year * battery_block.energy_capacity_adopted[node]
                    for node in nodes
                )
            )
            battery_block.objective_contribution = pyo.Expression(
                expr=battery_block.battery_capital_costs + battery_block.battery_fixed_operations_and_maintenance
            )
            battery_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    battery_block.om_per_kwh_year * resolved.existing_energy_capacity[node]
                    for node in nodes
                )
            )
        else:
            battery_block.battery_capital_costs = pyo.Expression(expr=0.0)
            battery_block.battery_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)
            battery_block.objective_contribution = pyo.Expression(expr=0.0)
            battery_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    battery_block.om_per_kwh_year * resolved.existing_energy_capacity[node]
                    for node in nodes
                )
            )

    model.battery_energy_storage = pyo.Block(rule=block_rule)
    return model.battery_energy_storage
