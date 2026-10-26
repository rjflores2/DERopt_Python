"""
Controllable hydro technology block: conventional dam hydro and pumped storage hydro
in one set-indexed formulation. A unit with zero pump capacity is a conventional dam;
there is no separate adoption decision (all hydro capacity is existing).

Follows the ``hydrokinetic`` / ``solar_pv`` set-indexed pattern (block set ``HYDRO``,
dispatch indexed ``[node, unit, scenario, t]``, capacity indexed ``[node, unit]``) rather
than the single-instance pattern used by the storage blocks, because a case may model
several distinct reservoirs. The inventory balance itself is a direct extension of
``battery_energy_storage/block.py``'s cyclic wraparound, generalized to two additional
optional boundary conditions (``fixed_endpoints``, ``free_end``).

Units: inventory, inflow, and spill are kWh of stored energy potential (not volume, see
``requirements/controllable_hydro_spec.md`` section 4); turbine generation and pump
consumption are kWh of electricity at the bus; capacities are kW.

Zero-capacity ``(node, unit)`` pairs are permitted and inert (mirrors
``existing_solar_capacity[node, profile]`` in ``solar_pv``): a unit is understood to be
located at the node(s) where its capacity is non-zero, and dispatch is forced to zero
everywhere else by the turbine/pump limit constraints.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer
from shared.cost_helpers import (
    annualized_fixed_cost_by_node_category,
    attach_standard_cost_expressions,
)
from shared.scenario_helpers import series_for_scenario

from .inputs import (
    BOUNDARY_CYCLIC,
    BOUNDARY_FIXED_ENDPOINTS,
    resolve_controllable_hydro_block_inputs,
)


def add_controllable_hydro_block(
    model: pyo.Block,
    data: DataContainer,
    *,
    controllable_hydro_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """Build and attach the controllable_hydro block (one HYDRO unit set, one node index
    per load bus, one time index per period). ``financials`` is accepted for interface
    parity with other registry hooks but unused: hydro capacity here is existing-only, so
    there is no capital annualization.

    1. Data and other inputs
       - ``data.static['controllable_hydro_unit_keys']`` -> ordered unit keys (initializes ``HYDRO``)
       - ``data.static['controllable_hydro_inflow_keys']`` -> maps unit key to its inflow timeseries key
       - ``data.timeseries[<inflow_key>]`` -> inflow in kWh of stored energy potential per timestep
       - ``controllable_hydro_params`` -> user options merged with defaults (see ``inputs.py``)

    2. Sets
       - ``hydro_block.HYDRO`` -> unit index

    3. Variables
       - ``reservoir_inventory[node, unit, scenario, t]`` -> stored energy potential (kWh)
       - ``turbine_generation[node, unit, scenario, t]`` -> electricity at the bus (kWh/timestep)
       - ``pump_consumption[node, unit, scenario, t]`` -> electricity drawn to pump (kWh/timestep)
       - ``spill[node, unit, scenario, t]`` -> non-negative, no cost, no upper bound

    4. Parameters
       - ``turbine_efficiency`` / ``pump_efficiency`` -> scalar, model-wide (not per-unit)
       - ``inventory_retention`` -> scalar per-timestep retention factor
       - ``minimum_soc_fraction`` / ``maximum_soc_fraction`` -> scalar inventory window fractions
       - ``reservoir_capacity_kwh[node, unit]`` / ``turbine_capacity_kw[node, unit]`` /
         ``pump_capacity_kw[node, unit]`` -> existing capacities
       - ``inflow_kwh[unit, scenario, t]`` -> per-site inflow (not per-node)
       - ``variable_om_per_kwh[unit]`` / ``fixed_om_per_kw_year[unit]`` -> per-unit cost rates

    5. Contribution to electricity sources and sinks
       - ``electricity_source_term[node, scenario, t]`` -> sum of turbine_generation over HYDRO
       - ``electricity_sink_term[node, scenario, t]`` -> sum of pump_consumption over HYDRO

    6. Contribution to the cost function
       - ``variable_operating_cost`` -> probability-weighted variable O&M on turbine generation
       - ``fixed_om_existing`` -> fixed O&M on turbine capacity (existing only; no adoption)

    7. Constraints
       - ``inventory_minimum`` / ``inventory_maximum`` -> SOC window as fractions of reservoir capacity
       - ``turbine_limit`` / ``pump_limit`` -> dispatch <= capacity_kw * time_step_hours
       - ``inventory_balance`` -> cyclic (all t) or open (t >= 2, per boundary_condition)
       - ``initial_inventory`` / ``final_inventory`` -> boundary anchors for fixed_endpoints / free_end
       - ``minimum_release`` -> only for (unit, t) pairs with a non-zero minimum release
    """
    T = model.T
    nodes = list(model.NODES)
    horizon = list(T)
    time_index = {t: i for i, t in enumerate(horizon)}
    n_time = len(horizon)

    unit_keys = list(data.static.get("controllable_hydro_unit_keys") or [])
    if not unit_keys:
        raise ValueError(
            "controllable_hydro block requires data.static['controllable_hydro_unit_keys'] "
            "(load hydro data first)"
        )

    inflow_keys_by_unit = data.static.get("controllable_hydro_inflow_keys") or {}
    missing_inflow_units = [u for u in unit_keys if u not in inflow_keys_by_unit]
    if missing_inflow_units:
        raise ValueError(
            "controllable_hydro: data.static['controllable_hydro_inflow_keys'] is missing "
            f"entries for units {missing_inflow_units!r}"
        )

    scenario_keys = list(data.scenario_keys)
    inflow_init: dict[tuple[str, str, int], float] = {}
    for unit in unit_keys:
        inflow_key = inflow_keys_by_unit[unit]
        for s in scenario_keys:
            series = series_for_scenario(data, s, inflow_key)
            if len(series) != n_time:
                raise ValueError(
                    f"controllable_hydro: unit {unit!r} scenario {s!r} inflow series "
                    f"{inflow_key!r} length {len(series)} != horizon length {n_time}"
                )
            for t in horizon:
                inflow_init[(unit, s, t)] = float(series[t])

    resolved = resolve_controllable_hydro_block_inputs(
        controllable_hydro_params,
        nodes=nodes,
        units=unit_keys,
        time_indices=horizon,
    )
    boundary_condition = resolved.boundary_condition

    def block_rule(hydro_block):
        hydro_block.HYDRO = pyo.Set(initialize=unit_keys, ordered=True)

        hydro_block.turbine_efficiency = pyo.Param(
            initialize=resolved.turbine_efficiency, within=pyo.PercentFraction, mutable=False
        )
        hydro_block.pump_efficiency = pyo.Param(
            initialize=resolved.pump_efficiency, within=pyo.PercentFraction, mutable=False
        )
        hydro_block.inventory_retention = pyo.Param(
            initialize=resolved.inventory_retention, within=pyo.PercentFraction, mutable=False
        )
        hydro_block.minimum_soc_fraction = pyo.Param(
            initialize=resolved.minimum_soc_fraction, within=pyo.PercentFraction, mutable=False
        )
        hydro_block.maximum_soc_fraction = pyo.Param(
            initialize=resolved.maximum_soc_fraction, within=pyo.PercentFraction, mutable=False
        )

        hydro_block.reservoir_capacity_kwh = pyo.Param(
            nodes,
            hydro_block.HYDRO,
            initialize=resolved.reservoir_capacity_kwh,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        hydro_block.turbine_capacity_kw = pyo.Param(
            nodes,
            hydro_block.HYDRO,
            initialize=resolved.turbine_capacity_kw,
            within=pyo.NonNegativeReals,
            mutable=False,
        )
        hydro_block.pump_capacity_kw = pyo.Param(
            nodes,
            hydro_block.HYDRO,
            initialize=resolved.pump_capacity_kw,
            within=pyo.NonNegativeReals,
            mutable=False,
        )

        hydro_block.variable_om_per_kwh = pyo.Param(
            hydro_block.HYDRO,
            initialize=resolved.variable_om_per_kwh,
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hydro_block.fixed_om_per_kw_year = pyo.Param(
            hydro_block.HYDRO,
            initialize=resolved.fixed_om_per_kw_year,
            within=pyo.NonNegativeReals,
            mutable=True,
        )

        # Inflow is a property of the site, not the node (see spec section 3): indexed
        # [unit, scenario, t], mirroring hydrokinetic.yield_kwh_per_m2[profile, scenario, t].
        hydro_block.inflow_kwh = pyo.Param(
            hydro_block.HYDRO,
            model.SCENARIOS,
            T,
            initialize=inflow_init,
            within=pyo.NonNegativeReals,
            mutable=False,
        )

        # Scenario-indexed (Stage-2 dispatch): each scenario gets its own independent
        # inventory trajectory, including its own cyclic wraparound (see
        # battery_energy_storage/block.py for the detailed note on why this stays
        # scenario-safe as long as every reference below carries the same s).
        hydro_block.reservoir_inventory = pyo.Var(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, within=pyo.NonNegativeReals
        )
        hydro_block.turbine_generation = pyo.Var(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, within=pyo.NonNegativeReals
        )
        hydro_block.pump_consumption = pyo.Var(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, within=pyo.NonNegativeReals
        )
        # No cost, no upper bound: spill is what guarantees the cyclic case stays feasible
        # in a wet period (see spec section 13).
        hydro_block.spill = pyo.Var(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, within=pyo.NonNegativeReals
        )

        def inventory_minimum_rule(m, node, unit, s, t):
            return (
                m.reservoir_inventory[node, unit, s, t]
                >= m.minimum_soc_fraction * m.reservoir_capacity_kwh[node, unit]
            )

        def inventory_maximum_rule(m, node, unit, s, t):
            return (
                m.reservoir_inventory[node, unit, s, t]
                <= m.maximum_soc_fraction * m.reservoir_capacity_kwh[node, unit]
            )

        hydro_block.inventory_minimum = pyo.Constraint(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, rule=inventory_minimum_rule
        )
        hydro_block.inventory_maximum = pyo.Constraint(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, rule=inventory_maximum_rule
        )

        def turbine_limit_rule(m, node, unit, s, t):
            return m.turbine_generation[node, unit, s, t] <= (
                m.turbine_capacity_kw[node, unit] * model.time_step_hours
            )

        def pump_limit_rule(m, node, unit, s, t):
            return m.pump_consumption[node, unit, s, t] <= (
                m.pump_capacity_kw[node, unit] * model.time_step_hours
            )

        hydro_block.turbine_limit = pyo.Constraint(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, rule=turbine_limit_rule
        )
        hydro_block.pump_limit = pyo.Constraint(
            nodes, hydro_block.HYDRO, model.SCENARIOS, T, rule=pump_limit_rule
        )

        def inventory_balance_rule(m, node, unit, s, t):
            time_step_idx = time_index[t]
            previous_time_step = horizon[-1] if time_step_idx == 0 else horizon[time_step_idx - 1]
            return m.reservoir_inventory[node, unit, s, t] == (
                m.inventory_retention * m.reservoir_inventory[node, unit, s, previous_time_step]
                + m.inflow_kwh[unit, s, t]
                + m.pump_efficiency * m.pump_consumption[node, unit, s, t]
                - (1.0 / m.turbine_efficiency) * m.turbine_generation[node, unit, s, t]
                - m.spill[node, unit, s, t]
            )

        if boundary_condition == BOUNDARY_CYCLIC:
            # Same wraparound as battery_energy_storage/block.py: the balance applies at
            # every t, and t-1 is the last timestep of the horizon when t is the first.
            hydro_block.inventory_balance = pyo.Constraint(
                nodes, hydro_block.HYDRO, model.SCENARIOS, T, rule=inventory_balance_rule
            )
        else:
            # fixed_endpoints / free_end: the balance applies for t >= 2 only.
            non_first_periods = horizon[1:]
            hydro_block.inventory_balance = pyo.Constraint(
                nodes, hydro_block.HYDRO, model.SCENARIOS, non_first_periods, rule=inventory_balance_rule
            )

            first_t = horizon[0]

            def initial_inventory_rule(m, node, unit, s):
                return m.reservoir_inventory[node, unit, s, first_t] == (
                    resolved.start_soc_fraction * m.reservoir_capacity_kwh[node, unit]
                )

            hydro_block.initial_inventory = pyo.Constraint(
                nodes, hydro_block.HYDRO, model.SCENARIOS, rule=initial_inventory_rule
            )

            if boundary_condition == BOUNDARY_FIXED_ENDPOINTS:
                last_t = horizon[-1]

                def final_inventory_rule(m, node, unit, s):
                    return m.reservoir_inventory[node, unit, s, last_t] == (
                        resolved.end_soc_fraction * m.reservoir_capacity_kwh[node, unit]
                    )

                hydro_block.final_inventory = pyo.Constraint(
                    nodes, hydro_block.HYDRO, model.SCENARIOS, rule=final_inventory_rule
                )

        # Minimum release: both terms are in stored energy potential, so turbine release
        # enters divided by turbine efficiency (spec section 5). Skip entirely for (unit, t)
        # pairs with no minimum release, rather than adding a trivially satisfied row.
        min_release_kwh = resolved.minimum_release_kwh
        if min_release_kwh:
            min_release_index = [
                (node, unit, s, t)
                for node in nodes
                for (unit, t) in min_release_kwh
                for s in model.SCENARIOS
            ]
            hydro_block.MINIMUM_RELEASE_INDEX = pyo.Set(
                dimen=4, initialize=min_release_index, ordered=True
            )

            def minimum_release_rule(m, node, unit, s, t):
                return (
                    m.turbine_generation[node, unit, s, t] / m.turbine_efficiency
                    + m.spill[node, unit, s, t]
                ) >= min_release_kwh[(unit, t)]

            hydro_block.minimum_release = pyo.Constraint(
                hydro_block.MINIMUM_RELEASE_INDEX, rule=minimum_release_rule
            )

        hydro_block.electricity_source_term = pyo.Expression(
            nodes,
            model.SCENARIOS,
            T,
            rule=lambda m, node, s, t: sum(m.turbine_generation[node, d, s, t] for d in m.HYDRO),
        )
        hydro_block.electricity_sink_term = pyo.Expression(
            nodes,
            model.SCENARIOS,
            T,
            rule=lambda m, node, s, t: sum(m.pump_consumption[node, d, s, t] for d in m.HYDRO),
        )

        # Variable O&M is keyed by [node, unit, scenario, t] with a per-unit rate; kept
        # inline (as hydrokinetic does) because the shared time_summed_variable_cost
        # helper only supports a plain [node, scenario, t] flow shape. Probability-weighted
        # across scenarios (Stage-2 recourse cost). Do not pass dt_hours to any shared
        # helper here: flow variables are already kWh per timestep (see spec section 15).
        variable_operating_cost = pyo.Expression(
            expr=sum(
                model.scenario_probability[s]
                * hydro_block.variable_om_per_kwh[unit]
                * hydro_block.turbine_generation[node, unit, s, t]
                for node in nodes
                for unit in hydro_block.HYDRO
                for s in model.SCENARIOS
                for t in T
            )
        )

        fixed_om_existing = annualized_fixed_cost_by_node_category(
            cost_per_unit_by_category=hydro_block.fixed_om_per_kw_year,
            capacity_var_by_node_category=hydro_block.turbine_capacity_kw,
            nodes=nodes,
            categories=hydro_block.HYDRO,
        )

        attach_standard_cost_expressions(
            hydro_block,
            allow_adoption=False,
            fixed_om_existing=fixed_om_existing,
            variable_operating_cost=variable_operating_cost,
        )

    model.controllable_hydro = pyo.Block(rule=block_rule)
    return model.controllable_hydro
