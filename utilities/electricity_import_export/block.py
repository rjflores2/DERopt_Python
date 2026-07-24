"""Pyomo grid / utility import block (variables, constraints, costs)."""

from __future__ import annotations

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .demand_charge_indexing import (
    flat_demand_nodes_and_rates_for_month,
    sorted_year_month_keys,
    times_by_year_month_from_datetimes,
    tou_demand_tier_groups_for_month,
)
from .inputs import resolve_utility_inputs


def add_utility_block(model: pyo.Block, data: DataContainer) -> pyo.Block | None:
    """
    Build and attach the grid / utility import block when energy prices, demand charges,
    and/or fixed customer charges apply; otherwise return ``None``.

    Assumption used in this layer: each ``node`` represents one customer/meter for utility billing.
    Under this assumption, energy prices, demand charges, and fixed customer charges are applied per node.

    1. Data and other inputs
       - ``model.import_prices_by_node``            -> optional per-node base import price vectors ($/kWh)
       - ``model.utility_rate_by_node``             -> optional per-node base parsed tariff objects
       - ``data.scenario_import_prices_by_node`` / ``data.scenario_utility_rate_by_node`` -> optional
         per-scenario overrides (a scenario without its own override falls back to the base above)
       - ``data.static["time_step_hours"]``         -> required when any demand charges are active (used to convert kWh/period to kW)
       - ``data.timeseries["datetime"]``            -> required when any demand charges are active (used to map timesteps into bill months and TOU tiers); also used to prorate fixed customer charges across the represented horizon

    2. Sets (Pyomo ``Set``)
       - ``model.T``                                -> time index used by the utility block
       - ``model.NODES``                            -> node index used by the utility block
       - ``model.SCENARIOS``                        -> scenario index (two-stage stochastic; each scenario incurs and pays its own realized monthly peak / energy cost, a Stage-2 recourse cost)
       - ``flat_peak_index``                        -> ``(year, month, node, scenario)`` tuples that receive a flat-demand peak variable
       - ``flat_ub_index``                          -> ``(year, month, node, scenario, t)`` tuples used as the flat demand-charge upper-bound constraint index (``t`` restricted to that month)
       - ``tou_peak_index``                         -> ``(year, month, tier, node, scenario)`` tuples that receive a TOU demand peak variable
       - ``tou_ub_index``                           -> ``(year, month, tier, node, scenario, t)`` tuples used as the TOU demand-charge upper-bound constraint index (``t`` restricted to that tier's hours in that month for that node)

    3. Variables (Pyomo ``Var``)
       - ``grid_import[node, scenario, t]``         -> grid energy imported at each node, scenario, and time period (kWh/period)
       - ``P_flat[year, month, node, scenario]``    -> flat-demand peak proxy (kW) for one bill month, node, and scenario; enforces a monthly peak envelope
       - ``P_tou[year, month, tier, node, scenario]`` -> TOU demand peak proxy (kW) for one bill month, TOU tier, node, and scenario; enforces a per-month-per-tier peak envelope

    4. Parameters and Expressions
       - ``import_price[node, scenario, t]``        -> node/scenario-specific import price ($/kWh) for each time period
       - ``flat_demand_rate[year, month, node, scenario]``    -> flat $/kW rate applied to ``P_flat[year, month, node, scenario]``
       - ``tou_demand_rate[year, month, tier, node, scenario]`` -> TOU $/kW rate applied to ``P_tou[year, month, tier, node, scenario]``
       - ``grid_import_power_kw[node, scenario, t]`` -> grid import power proxy used only for demand charges: ``grid_import / time_step_hours`` (kW)

    5. Contribution to electricity sources - ``electricity_source_term[node, scenario, t]``
       - ``grid_import[node, scenario, t]``         -> utility block source-side contribution to the electricity balance in ``model.core``

    6. Contribution to the cost function - ``objective_contribution`` (probability-weighted across scenarios)
       - ``energy_import_cost``                     -> expected energy-import cost from ``scenario_probability[s] * import_price[node,s,t] * grid_import[node,s,t]``
       - ``nonTOU_Demand_Charge_Cost``               -> expected cost, sum over ``flat_peak_index`` of ``scenario_probability[s] * flat_demand_rate * P_flat``
       - ``TOU_Demand_Charge_Cost``                  -> expected cost, sum over ``tou_peak_index`` of ``scenario_probability[s] * tou_demand_rate * P_tou``
       - ``fixed_usd``                              -> fixed customer-charge USD over the represented horizon from ``fixed_customer_charges_horizon_usd``; scenario-independent (see ``inputs.ResolvedUtilityInputs`` docstring)

    7. Contribution to reporting - ``cost_non_optimizing_annual``
       - fixed customer-charge portion only (scalar, scenario-independent); this is the usage-independent utility-fee term billed per node

    8. Constraints
       - ``flat_demand_charge_ub[year, month, node, scenario, t]``      -> ``P_flat[year, month, node, scenario] >= grid_import_power_kw[node, scenario, t]`` for each timestep ``t`` in that ``(year, month)``
       - ``tou_demand_charge_ub[year, month, tier, node, scenario, t]`` -> ``P_tou[year, month, tier, node, scenario] >= grid_import_power_kw[node, scenario, t]`` for each ``t`` in that ``(year, month, tier, node)``'s hours
    """

    resolved = resolve_utility_inputs(model, data)
    if resolved is None:
        return None

    T = model.T  # Time from the model
    NODES = list(model.NODES)  # Nodes from the model
    SCENARIOS = list(model.SCENARIOS)  # Scenarios from the model
    prices_by_node_by_scenario = resolved.prices_by_node_by_scenario  # Energy charge prices by node, per scenario
    utility_tariff_by_node_by_scenario = resolved.utility_tariff_by_node_by_scenario  # Utility tariff by node, per scenario
    has_any_demand_charges = resolved.has_any_demand_charges  # Whether there are any demand charges (any scenario)
    dt_hours_f = resolved.dt_hours_f  # Time step hours from the model
    datetimes = resolved.datetimes  # Datetimes from the model
    fixed_usd = resolved.fixed_usd  # Fixed customer charges in USD (scenario-independent)
    time_indices = resolved.time_indices  # Time indices from the model

    # --- Build the demand-charge index tuples and rate dicts in plain Python. ---
    # The (year, month) boundary lives in the index tuples themselves: each P_flat / P_tou
    # element is only ever upper-bounded by timesteps drawn from its own (year, month)
    # bucket (and, for TOU, its own tier hours), preserving per-month billing semantics.
    # Each scenario gets its own peak/rate keys, built from that scenario's own resolved
    # tariff -- a scenario-level utility_tariffs override can change which nodes have demand
    # charges at all, not just the price, so this must be recomputed per scenario rather
    # than reusing one shared index across scenarios.
    flat_peak_keys: list[tuple[int, int, str, str]] = []
    flat_rate_by_key: dict[tuple[int, int, str, str], float] = {}
    flat_ub_keys: list[tuple[int, int, str, str, int]] = []

    tou_peak_keys: list[tuple[int, int, int, str, str]] = []
    tou_rate_by_key: dict[tuple[int, int, int, str, str], float] = {}
    tou_ub_keys: list[tuple[int, int, int, str, str, int]] = []

    if has_any_demand_charges:
        times_by_year_month = times_by_year_month_from_datetimes(datetimes, time_indices)
        year_months_in_run = sorted_year_month_keys(times_by_year_month)

        for s in SCENARIOS:
            utility_tariff_by_node = utility_tariff_by_node_by_scenario[s]
            for calendar_year, month_index in year_months_in_run:
                times_in_month = times_by_year_month[(calendar_year, month_index)]
                if not times_in_month:
                    continue

                # Flat demand: one peak per (year, month, node, scenario) with a positive $/kW rate.
                flat_nodes, flat_rate_by_node = flat_demand_nodes_and_rates_for_month(
                    NODES, utility_tariff_by_node, month_index
                )
                for node in flat_nodes:
                    rate = float(flat_rate_by_node.get(node, 0.0))
                    # OpenEI can list flat demand with $/kW == 0 (no charge for that month/structure) — skip vars/constraints.
                    if rate <= 0.0:
                        continue
                    key = (calendar_year, month_index, node, s)
                    flat_peak_keys.append(key)
                    flat_rate_by_key[key] = rate
                    for t in times_in_month:
                        flat_ub_keys.append((calendar_year, month_index, node, s, t))

                # TOU demand: one peak per (year, month, tier, node, scenario); hours are per-node within the tier.
                for group in tou_demand_tier_groups_for_month(
                    times_in_month, datetimes, NODES, utility_tariff_by_node
                ):
                    tier_index = group.tier_index
                    for node in group.tier_nodes:
                        rate = float(group.rate_by_node.get(node, 0.0))
                        # OpenEI often uses $/kW == 0 for a tier or period meaning no demand charge there — skip vars/constraints.
                        if rate <= 0.0:
                            continue
                        key = (calendar_year, month_index, tier_index, node, s)
                        tou_peak_keys.append(key)
                        tou_rate_by_key[key] = rate
                        for t in group.times_by_node[node]:
                            tou_ub_keys.append((calendar_year, month_index, tier_index, node, s, t))

    def block_rule(utility_block):  # Pyomo block for utility import/export/possibly demand charges
        # --- Grid import (kWh/period): shared by energy charges, demand peaks, and the electricity
        # balance. Scenario-indexed (Stage-2 dispatch): each scenario gets its own import trajectory. ---
        utility_block.grid_import = pyo.Var(NODES, SCENARIOS, T, within=pyo.NonNegativeReals)

        # --- Demand charges: average kW over each period from kWh/period (only if any node has demand charges) ---
        if has_any_demand_charges:
            if dt_hours_f is None:
                raise RuntimeError(
                    "Internal error: dt_hours_f must be set when demand charges are active."
                )
            utility_block.grid_import_power_kw = pyo.Expression(
                NODES,
                SCENARIOS,
                T,
                rule=lambda m, node, s, t: m.grid_import[node, s, t] / dt_hours_f,
            )

        utility_block.electricity_source_term = pyo.Expression(
            NODES,
            SCENARIOS,
            T,
            rule=lambda m, node, s, t: m.grid_import[node, s, t],
        )

        # --- Energy charges: $/kWh × kWh imported (per node, scenario, time) ---
        utility_block.import_price = pyo.Param(
            NODES,
            SCENARIOS,
            T,
            initialize={
                (node, s, t): float(prices_by_node_by_scenario[s][node][t])
                for node in NODES
                for s in SCENARIOS
                for t in T
            },
            within=pyo.Reals,
            mutable=False,
        )
        utility_block.energy_import_cost = pyo.Expression(
            expr=sum(
                model.scenario_probability[s]
                * utility_block.import_price[node, s, t]
                * utility_block.grid_import[node, s, t]
                for node in NODES
                for s in SCENARIOS
                for t in T
            )
        )

        ###########################
        ### Flat demand charges ###
        ###########################
        utility_block.flat_peak_index = pyo.Set(
            dimen=4, initialize=flat_peak_keys, ordered=True
        )
        utility_block.flat_ub_index = pyo.Set(
            dimen=5, initialize=flat_ub_keys, ordered=True
        )
        utility_block.P_flat = pyo.Var(
            utility_block.flat_peak_index, within=pyo.NonNegativeReals
        )
        utility_block.flat_demand_rate = pyo.Param(
            utility_block.flat_peak_index,
            initialize=flat_rate_by_key,
            within=pyo.NonNegativeReals,
            mutable=False,
        )

        def _flat_ub_rule(b, year, month, node, s, t):
            # Monthly envelope: the (year, month, scenario) peak must cover every timestep in that month.
            return b.P_flat[year, month, node, s] >= b.grid_import_power_kw[node, s, t]

        utility_block.flat_demand_charge_ub = pyo.Constraint(
            utility_block.flat_ub_index, rule=_flat_ub_rule
        )

        utility_block.nonTOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(
                model.scenario_probability[s]
                * utility_block.flat_demand_rate[year, month, node, s]
                * utility_block.P_flat[year, month, node, s]
                for (year, month, node, s) in utility_block.flat_peak_index
            )
        )

        ##########################
        ### TOU demand charges ###
        ##########################
        utility_block.tou_peak_index = pyo.Set(
            dimen=5, initialize=tou_peak_keys, ordered=True
        )
        utility_block.tou_ub_index = pyo.Set(
            dimen=6, initialize=tou_ub_keys, ordered=True
        )
        utility_block.P_tou = pyo.Var(
            utility_block.tou_peak_index, within=pyo.NonNegativeReals
        )
        utility_block.tou_demand_rate = pyo.Param(
            utility_block.tou_peak_index,
            initialize=tou_rate_by_key,
            within=pyo.NonNegativeReals,
            mutable=False,
        )

        def _tou_ub_rule(b, year, month, tier, node, s, t):
            # Monthly-by-tier envelope: peak only sees this tier's hours within this (year, month, scenario).
            return b.P_tou[year, month, tier, node, s] >= b.grid_import_power_kw[node, s, t]

        utility_block.tou_demand_charge_ub = pyo.Constraint(
            utility_block.tou_ub_index, rule=_tou_ub_rule
        )

        utility_block.TOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(
                model.scenario_probability[s]
                * utility_block.tou_demand_rate[year, month, tier, node, s]
                * utility_block.P_tou[year, month, tier, node, s]
                for (year, month, tier, node, s) in utility_block.tou_peak_index
            )
        )

        # --- Optimizing objective: energy + demand (fixed fees are reported separately) ---
        utility_block.objective_contribution = pyo.Expression(
            expr=(
                utility_block.energy_import_cost
                + utility_block.nonTOU_Demand_Charge_Cost
                + utility_block.TOU_Demand_Charge_Cost
            )
        )
        # --- Fixed customer charges (USD over horizon; non-optimizing / reporting) ---
        utility_block.cost_non_optimizing_annual = pyo.Expression(expr=fixed_usd)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


def register(model: pyo.Block, data: DataContainer) -> pyo.Block | None:
    """Registry hook used by ``model.core``."""
    return add_utility_block(model, data)
