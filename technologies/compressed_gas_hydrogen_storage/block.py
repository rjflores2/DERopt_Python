"""
Compressed-gas hydrogen storage technology block.

Modeled analogously to ``battery_energy_storage``, but the state variable is **hydrogen inventory**
(**kWh-H2_LHV**) instead of electrical SOC. Charge and discharge flows are **kWh-H2_LHV per timestep**
at the hydrogen balance boundary.

**LHV basis only; HHV is not used.**

Unlike the battery, **charging hydrogen into this storage** draws **auxiliary electricity**
(compressor work) from the electricity balance: ``electricity_sink_term = compression coefficient ×
hydrogen_charge_flow``. The coefficient ``compression_kwh_electric_per_kwh_h2_lhv`` is documented as
**compressed-gas** auxiliary load per kWh-H2 (LHV) charged; liquefaction is **not** modeled explicitly here.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import resolve_compressed_gas_hydrogen_storage_block_inputs


def add_compressed_gas_hydrogen_storage_block(
    model: Any,
    data: Any,
    *,
    compressed_gas_hydrogen_storage_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach compressed-gas hydrogen storage (one node per load bus, one time index per period).

    1. Data and other inputs
       - ``model.NODES``, ``model.T``
       - ``compressed_gas_hydrogen_storage_params`` / ``financials``

    2. Sets
       - ``model.T``, ``model.NODES``

    3. Variables
       - ``hydrogen_inventory_kwh_h2_lhv[node, t]`` — stored hydrogen energy (LHV)
       - ``hydrogen_charge_flow[node, t]`` — kWh-H2_LHV per timestep charged (withdrawn from H2 balance)
       - ``hydrogen_discharge_flow[node, t]`` — kWh-H2_LHV per timestep discharged (injected to H2 balance)
       - ``energy_capacity_adopted_kwh_h2_lhv[node]`` when adoption is enabled

    4. Parameters
       - Efficiencies, retention, min/max inventory fractions, C-rate-style limits on charge/discharge
       - ``compression_kwh_electric_per_kwh_h2_lhv`` — kWh-electric per kWh-H2_LHV charged
       - ``existing_energy_capacity_kwh_h2_lhv[node]``, capital and O&M, ``amortization_factor``
       - Optional ``initial_hydrogen_inventory_fraction``

    5. Named expressions
       - ``total_energy_capacity_kwh_h2_lhv[node]`` — tank energy capacity (LHV basis)

    6. Hydrogen balance
       - ``hydrogen_sink_term`` = ``hydrogen_charge_flow``
       - ``hydrogen_source_term`` = ``hydrogen_discharge_flow``

    7. Electricity balance
       - ``electricity_sink_term`` = ``compression_kwh_electric_per_kwh_h2_lhv * hydrogen_charge_flow``

    8. Objective
       - Adopted capacity capital + fixed O&M; ``cost_non_optimizing_annual`` for existing capacity

    9. Constraints
       - Inventory bounds, charge/discharge limits vs capacity, rolling inventory balance (cyclic like battery)
       - Optional initial inventory anchor
    """
    T = model.T
    nodes = list(model.NODES)
    horizon = list(T)
    time_index = {t: i for i, t in enumerate(horizon)}

    allow_adoption = (compressed_gas_hydrogen_storage_params or {}).get("allow_adoption", True)
    resolved = resolve_compressed_gas_hydrogen_storage_block_inputs(
        compressed_gas_hydrogen_storage_params,
        financials=financials,
        nodes=nodes,
    )

    def block_rule(h2_block):
        h2_block.charge_efficiency = pyo.Param(
            initialize=resolved.charge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.discharge_efficiency = pyo.Param(
            initialize=resolved.discharge_efficiency,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.max_hydrogen_charge_per_kwh_capacity = pyo.Param(
            initialize=resolved.max_hydrogen_charge_per_kwh_capacity,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.max_hydrogen_discharge_per_kwh_capacity = pyo.Param(
            initialize=resolved.max_hydrogen_discharge_per_kwh_capacity,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.hydrogen_inventory_retention = pyo.Param(
            initialize=resolved.hydrogen_inventory_retention,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.minimum_hydrogen_inventory_fraction = pyo.Param(
            initialize=resolved.minimum_hydrogen_inventory_fraction,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.maximum_hydrogen_inventory_fraction = pyo.Param(
            initialize=resolved.maximum_hydrogen_inventory_fraction,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.existing_energy_capacity_kwh_h2_lhv = pyo.Param(
            nodes,
            initialize=resolved.existing_energy_capacity_kwh_h2_lhv,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.capital_cost_per_kwh_h2_lhv = pyo.Param(
            initialize=resolved.capital_cost_per_kwh_h2_lhv,
            within=pyo.Reals,
            mutable=True,
        )
        h2_block.om_per_kwh_h2_lhv_year = pyo.Param(
            initialize=resolved.om_per_kwh_h2_lhv_year,
            within=pyo.Reals,
            mutable=True,
        )
        h2_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        h2_block.compression_kwh_electric_per_kwh_h2_lhv = pyo.Param(
            initialize=resolved.compression_kwh_electric_per_kwh_h2_lhv,
            within=pyo.NonNegativeReals,
            mutable=True,
        )

        if resolved.initial_hydrogen_inventory_fraction is not None:
            h2_block.initial_hydrogen_inventory_fraction = pyo.Param(
                initialize=resolved.initial_hydrogen_inventory_fraction,
                within=pyo.NonNegativeReals,
                mutable=True,
            )

        h2_block.hydrogen_inventory_kwh_h2_lhv = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        h2_block.hydrogen_charge_flow = pyo.Var(nodes, T, within=pyo.NonNegativeReals)
        h2_block.hydrogen_discharge_flow = pyo.Var(nodes, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            h2_block.energy_capacity_adopted_kwh_h2_lhv = pyo.Var(nodes, within=pyo.NonNegativeReals)

            def total_energy_capacity_kwh_h2_lhv(m, node):
                return m.existing_energy_capacity_kwh_h2_lhv[node] + m.energy_capacity_adopted_kwh_h2_lhv[node]

        else:

            def total_energy_capacity_kwh_h2_lhv(m, node):
                return m.existing_energy_capacity_kwh_h2_lhv[node]

        h2_block.total_energy_capacity_kwh_h2_lhv = pyo.Expression(nodes, rule=total_energy_capacity_kwh_h2_lhv)

        def inventory_minimum_rule(m, node, t):
            return (
                m.hydrogen_inventory_kwh_h2_lhv[node, t]
                >= m.minimum_hydrogen_inventory_fraction * m.total_energy_capacity_kwh_h2_lhv[node]
            )

        def inventory_maximum_rule(m, node, t):
            return (
                m.hydrogen_inventory_kwh_h2_lhv[node, t]
                <= m.maximum_hydrogen_inventory_fraction * m.total_energy_capacity_kwh_h2_lhv[node]
            )

        h2_block.hydrogen_inventory_minimum = pyo.Constraint(nodes, T, rule=inventory_minimum_rule)
        h2_block.hydrogen_inventory_maximum = pyo.Constraint(nodes, T, rule=inventory_maximum_rule)

        def hydrogen_charge_limit_rule(m, node, t):
            return m.hydrogen_charge_flow[node, t] <= (
                m.max_hydrogen_charge_per_kwh_capacity * m.total_energy_capacity_kwh_h2_lhv[node]
            )

        def hydrogen_discharge_limit_rule(m, node, t):
            return m.hydrogen_discharge_flow[node, t] <= (
                m.max_hydrogen_discharge_per_kwh_capacity * m.total_energy_capacity_kwh_h2_lhv[node]
            )

        h2_block.hydrogen_charge_limit = pyo.Constraint(nodes, T, rule=hydrogen_charge_limit_rule)
        h2_block.hydrogen_discharge_limit = pyo.Constraint(nodes, T, rule=hydrogen_discharge_limit_rule)

        def hydrogen_inventory_balance_rule(m, node, t):
            time_step_index = time_index[t]
            previous_time_step = horizon[-1] if time_step_index == 0 else horizon[time_step_index - 1]
            return m.hydrogen_inventory_kwh_h2_lhv[node, t] == (
                m.hydrogen_inventory_retention * m.hydrogen_inventory_kwh_h2_lhv[node, previous_time_step]
                + m.charge_efficiency * m.hydrogen_charge_flow[node, t]
                - (1.0 / m.discharge_efficiency) * m.hydrogen_discharge_flow[node, t]
            )

        h2_block.hydrogen_energy_balance = pyo.Constraint(nodes, T, rule=hydrogen_inventory_balance_rule)

        if hasattr(h2_block, "initial_hydrogen_inventory_fraction") and horizon:
            first_time_step = horizon[0]

            def initial_inventory_rule(m, node):
                return m.hydrogen_inventory_kwh_h2_lhv[node, first_time_step] == (
                    m.initial_hydrogen_inventory_fraction * m.total_energy_capacity_kwh_h2_lhv[node]
                )

            h2_block.initial_hydrogen_inventory = pyo.Constraint(nodes, rule=initial_inventory_rule)

        h2_block.hydrogen_source_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.hydrogen_discharge_flow[node, t]
        )
        h2_block.hydrogen_sink_term = pyo.Expression(
            nodes, T, rule=lambda m, node, t: m.hydrogen_charge_flow[node, t]
        )
        h2_block.electricity_sink_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: m.compression_kwh_electric_per_kwh_h2_lhv * m.hydrogen_charge_flow[node, t],
        )

        if allow_adoption:
            h2_block.h2_storage_capital_costs = pyo.Expression(
                expr=sum(
                    h2_block.capital_cost_per_kwh_h2_lhv
                    * h2_block.energy_capacity_adopted_kwh_h2_lhv[node]
                    * h2_block.amortization_factor
                    for node in nodes
                )
            )
            h2_block.h2_storage_fixed_operations_and_maintenance = pyo.Expression(
                expr=sum(
                    h2_block.om_per_kwh_h2_lhv_year * h2_block.energy_capacity_adopted_kwh_h2_lhv[node]
                    for node in nodes
                )
            )
            h2_block.objective_contribution = pyo.Expression(
                expr=h2_block.h2_storage_capital_costs + h2_block.h2_storage_fixed_operations_and_maintenance
            )
            h2_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    h2_block.om_per_kwh_h2_lhv_year * h2_block.existing_energy_capacity_kwh_h2_lhv[node]
                    for node in nodes
                )
            )
        else:
            h2_block.h2_storage_capital_costs = pyo.Expression(expr=0.0)
            h2_block.h2_storage_fixed_operations_and_maintenance = pyo.Expression(expr=0.0)
            h2_block.objective_contribution = pyo.Expression(expr=0.0)
            h2_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    h2_block.om_per_kwh_h2_lhv_year * h2_block.existing_energy_capacity_kwh_h2_lhv[node]
                    for node in nodes
                )
            )

    model.compressed_gas_hydrogen_storage = pyo.Block(rule=block_rule)
    return model.compressed_gas_hydrogen_storage
