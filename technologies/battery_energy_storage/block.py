"""
Battery energy storage technology block.

Battery is modeled per node and per time step with:
- State of charge (energy in storage) at each node and time.
- Separate charging and discharging power variables at each node and time.
- An energy balance that links state of charge across time steps.
- Power limits that scale with installed energy capacity (C-rates).
- SOC kept between configurable fractions of total capacity (existing + adopted) at each node.

The block contributes to the system electricity balance via:
- electricity_source_term[node, t] (discharging adds to sources),
- electricity_sink_term[node, t]  (charging adds to sinks),
so it plugs directly into the electricity balance built in model.core.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer
from shared.cost_helpers import (
    annualized_fixed_cost_by_node,
    attach_standard_cost_expressions,
)

from .inputs import resolve_battery_block_inputs


def add_battery_energy_storage_block(
    model: pyo.Block,
    data: DataContainer,
    *,
    battery_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Battery Energy Storage block (one node per load bus, one time index
    per optimization period). Charge/discharge flow variables are in **kWh per timestep** (so
    the electricity balance, variable costs, and SOC update are all dimensionally consistent
    across sub-hourly and hourly timesteps); state of charge is in kWh.

    1. Data and other inputs
       - ``data.static["electricity_load_keys"]`` -> ordered node keys (already loaded on ``model.NODES``)
       - ``model.T`` -> time periods from ``model.core``
       - ``battery_params`` -> user options merged with defaults
       - ``financials`` -> used to annualize capital on adopted kWh capacity

    2. Sets (Pyomo ``Set``)
       - ``model.T`` -> time index used by the Battery block
       - ``model.NODES`` -> node index used by the Battery block

    3. Variables (Pyomo ``Var``)
       - ``state_of_charge[node, t]`` -> stored energy / state of charge (kWh)
       - ``charge_power[node, t]`` -> energy drawn from the grid/source to charge the battery
         during the timestep (**kWh per timestep**; name retained for continuity — think of it as
         the interval-averaged charging power multiplied by ``time_step_hours``)
       - ``discharge_power[node, t]`` -> energy delivered from the battery during the timestep
         (**kWh per timestep**; same convention as ``charge_power``)
       - ``energy_capacity_adopted[node]`` -> incremental storage energy capacity (kWh, only when adoption is enabled)

    4. Parameters (Pyomo ``Param``)
       - ``capital_cost_per_kwh`` -> one-time capital cost ($/kWh), annualized in objective via ``amortization_factor``
       - ``om_per_kwh_year`` -> fixed O&M ($/kWh-year)
       - ``charge_efficiency`` / ``discharge_efficiency`` -> charging/discharging efficiencies
       - ``max_charge_power_per_kwh`` / ``max_discharge_power_per_kwh`` -> C-rate limits (kW per kWh capacity)
       - ``state_of_charge_retention`` -> per-step SOC retention factor
       - ``minimum_state_of_charge`` / ``maximum_state_of_charge`` -> SOC bounds as fractions of capacity
       - ``existing_energy_capacity[node]`` -> existing storage energy capacity by node (kWh)
       - ``amortization_factor`` -> annualization factor applied to adopted-capacity capital cost
       - ``initial_soc_fraction`` -> optional first-step SOC anchor fraction (defined only when provided)

    5. Other named components on the block
       - ``total_energy_capacity[node]`` -> existing + adopted kWh, or existing only

    6. Contribution to electricity sources and sinks
       - ``electricity_source_term[node, t]`` -> discharge power
       - ``electricity_sink_term[node, t]`` -> charge power

    7. Contribution to the cost function
       - ``battery_capital_costs`` / ``battery_fixed_operations_and_maintenance`` (adopted kWh)
       - ``objective_contribution`` -> sum of those when adoption is enabled
       - reporting-only existing O&M in ``cost_non_optimizing_annual``

    8. Constraints
       - ``state_of_charge_minimum`` -> ``state_of_charge >= minimum_state_of_charge * total_energy_capacity``
       - ``state_of_charge_maximum`` -> ``state_of_charge <= maximum_state_of_charge * total_energy_capacity``
       - ``charge_power_limit`` / ``discharge_power_limit`` -> ``flow <= C_rate * capacity_kwh *
         time_step_hours`` (kWh/timestep bound from the kW C-rate)
       - ``energy_balance`` -> SOC update across the horizon (implemented by
         ``battery_energy_balance_rule``); since charge/discharge are already kWh/timestep,
         no ``dt`` multiplier is needed inside the update
       - ``initial_soc`` -> optional first-period SOC anchor
    """
    T = model.T # Time index from model.core
    nodes = list(model.NODES) # Node index from model.core
    horizon = list(T) 
    # O(1) predecessor lookup for energy balance (avoid horizon.index(t) in constraint rules).
    time_index = {t: i for i, t in enumerate(horizon)}

    #Checking if adoption is allowed - if yes, then we will build the block and enable adoptiong
    allow_adoption = (battery_params or {}).get("allow_adoption", True)
    #Determine which battery parameters to use
    resolved = resolve_battery_block_inputs(
        battery_params=battery_params,
        financials=financials,
        nodes=nodes,
    )

    def block_rule(battery_block):
        #Battery charging efficiency
        battery_block.charge_efficiency = pyo.Param(
            initialize=resolved.charge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery discharging efficeincy
        battery_block.discharge_efficiency = pyo.Param(
            initialize=resolved.discharge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery maximum charging power per kWh installed
        battery_block.max_charge_power_per_kwh = pyo.Param(
            initialize=resolved.max_charge_power_per_kwh,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery maximum discharging power per kWh installed
        battery_block.max_discharge_power_per_kwh = pyo.Param(
            initialize=resolved.max_discharge_power_per_kwh,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery state of charge retention factor   
        battery_block.state_of_charge_retention = pyo.Param(
            initialize=resolved.state_of_charge_retention,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery minimum state of charge
        battery_block.minimum_state_of_charge = pyo.Param(
            initialize=resolved.minimum_state_of_charge,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery maximum state of charge
        battery_block.maximum_state_of_charge = pyo.Param(
            initialize=resolved.maximum_state_of_charge,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery existing energy capacity
        battery_block.existing_energy_capacity = pyo.Param(
            nodes,
            initialize=resolved.existing_energy_capacity,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery capital cost per kWh
        battery_block.capital_cost_per_kwh = pyo.Param(
            initialize=resolved.capital_cost_per_kwh,
            within=pyo.Reals,
            mutable=True,
        )
        #Battery fixed O&M per kWh per year
        battery_block.om_per_kwh_year = pyo.Param(
            initialize=resolved.om_per_kwh_year,
            within=pyo.Reals,
            mutable=True,
        )
        #Battery amortization factor
        battery_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        #Battery initial SOC fraction
        if resolved.initial_soc_fraction is not None:
            battery_block.initial_soc_fraction = pyo.Param(
                initialize=resolved.initial_soc_fraction,
                within=pyo.NonNegativeReals,
                mutable=False,
            )

        battery_block.state_of_charge = pyo.Var(nodes, T, within=pyo.NonNegativeReals) #Battery SOC
        battery_block.charge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals) # Battery Charging
        battery_block.discharge_power = pyo.Var(nodes, T, within=pyo.NonNegativeReals) # Batery Discahrging

        ### If adoption is allowed, then include the adoption variable
        ### Both logical statements resolve potential existing battery 
        ### storage capacity for use in the model constraints
        if allow_adoption:
            battery_block.energy_capacity_adopted = pyo.Var(nodes, within=pyo.NonNegativeReals)

            def total_energy_capacity(m, node):
                return m.existing_energy_capacity[node] + m.energy_capacity_adopted[node]
        else:
            def total_energy_capacity(m, node):
                return m.existing_energy_capacity[node]

        ### Formalizing the battery total energy capacity as a pyomo expresion
        battery_block.total_energy_capacity = pyo.Expression(nodes, rule=total_energy_capacity)

        # Usable SOC window (fraction of total kWh capacity: existing + adopted at each node).
        # First constraint forced mininmum SOC to be capacity*min_limit
        def state_of_charge_minimum_rule(m, node, t):
            return (
                m.state_of_charge[node, t]
                >= m.minimum_state_of_charge * m.total_energy_capacity[node]
            )

        # Second constraint forced maximum SOC to be capacity*max_limit
        def state_of_charge_maximum_rule(m, node, t):
            return (
                m.state_of_charge[node, t]
                <= m.maximum_state_of_charge * m.total_energy_capacity[node]
            )
        ### Adding the constraints to the block
        battery_block.state_of_charge_minimum = pyo.Constraint(
            nodes, T, rule=state_of_charge_minimum_rule
        )
        battery_block.state_of_charge_maximum = pyo.Constraint(
            nodes, T, rule=state_of_charge_maximum_rule
        )

        ### Adding the charge and discharge power limits constraints.
        # C-rate (kW/kWh) × capacity (kWh) = kW nameplate charge/discharge power.
        # Multiply by ``time_step_hours`` to bound the kWh-per-timestep flow variable.
        def charge_power_limit_rule(m, node, t):
            return m.charge_power[node, t] <= (
                m.max_charge_power_per_kwh * m.total_energy_capacity[node] * model.time_step_hours
            )

        def discharge_power_limit_rule(m, node, t):
            return m.discharge_power[node, t] <= (
                m.max_discharge_power_per_kwh * m.total_energy_capacity[node] * model.time_step_hours
            )
        ### Adding the max charge/discharge limit constraints to the block
        battery_block.charge_power_limit = pyo.Constraint(nodes, T, rule=charge_power_limit_rule)
        battery_block.discharge_power_limit = pyo.Constraint(nodes, T, rule=discharge_power_limit_rule)

        ### Adding the energy balance constraint to the block
        def battery_energy_balance_rule(m, node, t):
            time_step_index = time_index[t]
            # Python list indexing for horizon[-1] is the last entry in horizon
            previous_time_step = horizon[-1] if time_step_index == 0 else horizon[time_step_index - 1]
            return m.state_of_charge[node, t] == (
                # Stored energy naturally decays each timestep by the retention factor.
                m.state_of_charge_retention * m.state_of_charge[node, previous_time_step]
                + m.charge_efficiency * m.charge_power[node, t]
                - (1.0 / m.discharge_efficiency) * m.discharge_power[node, t]
            )

        battery_block.energy_balance = pyo.Constraint(nodes, T, rule=battery_energy_balance_rule)

        if hasattr(battery_block, "initial_soc_fraction") and horizon:
            first_time_step = horizon[0]

            def initial_soc_rule(m, node):
                return m.state_of_charge[node, first_time_step] == (
                    m.initial_soc_fraction * m.total_energy_capacity[node]
                )

            battery_block.initial_soc = pyo.Constraint(nodes, rule=initial_soc_rule)

        #Battery electricity source term
        battery_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.discharge_power[node, t],
        )
        #Battery electricity sink term
        battery_block.electricity_sink_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.charge_power[node, t],
        )

        annualized_capital_if_adopted = None
        fixed_om_adopted_if_adopted = None
        if allow_adoption:
            annualized_capital_if_adopted = annualized_fixed_cost_by_node(
                cost_per_unit=battery_block.capital_cost_per_kwh,
                capacity_var=battery_block.energy_capacity_adopted,
                nodes=nodes,
                amortization_factor=battery_block.amortization_factor,
            )
            fixed_om_adopted_if_adopted = annualized_fixed_cost_by_node(
                cost_per_unit=battery_block.om_per_kwh_year,
                capacity_var=battery_block.energy_capacity_adopted,
                nodes=nodes,
            )

        fixed_om_existing = annualized_fixed_cost_by_node(
            cost_per_unit=battery_block.om_per_kwh_year,
            capacity_var=battery_block.existing_energy_capacity,
            nodes=nodes,
        )

        attach_standard_cost_expressions(
            battery_block,
            allow_adoption=allow_adoption,
            fixed_om_existing=fixed_om_existing,
            annualized_capital_if_adopted=annualized_capital_if_adopted,
            fixed_om_adopted_if_adopted=fixed_om_adopted_if_adopted,
        )

    model.battery_energy_storage = pyo.Block(rule=block_rule)
    return model.battery_energy_storage
