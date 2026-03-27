"""Pyomo grid / utility import block (variables, constraints, costs)."""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .demand_charge_indexing import (
    flat_demand_nodes_and_rates_for_month,
    sorted_year_month_keys,
    times_by_year_month_from_datetimes,
    tou_demand_tier_groups_for_month,
)
from .inputs import resolve_utility_inputs


def add_utility_block(model: Any, data: Any) -> pyo.Block | None:
    """
    Build and attach the grid / utility import block when energy prices, demand charges,
    and/or fixed customer charges apply; otherwise return ``None``.

    Assumption used in this layer: each ``node`` represents one customer/meter for utility billing.
    Under this assumption, energy prices, demand charges, and fixed customer charges are applied per node.

    1. Data and other inputs
       - ``model.import_prices_by_node``            -> optional per-node import price vectors ($/kWh)
       - ``model.utility_rate_by_node``             -> optional per-node parsed tariff objects
       - ``data.static["time_step_hours"]``         -> required when any demand charges are active (used to convert kWh/period to kW)
       - ``data.timeseries["datetime"]``            -> required when any demand charges are active (used to map timesteps into bill months and TOU tiers); also used to prorate fixed customer charges across the represented horizon

    2. Sets (Pyomo ``Set``)
       - ``model.T``                                -> time index used by the utility block
       - ``model.NODES``                            -> node index used by the utility block

    3. Variables (Pyomo ``Var``)
       - ``grid_import[node, t]``                   -> grid energy imported at each node and time period (kWh/period)
       - ``P_flat_y{year}_m{month}``                -> flat-demand peak proxy for an applicable (year, month); indexed by nodes with flat demand charges
       - ``P_tou_y{year}_m{month}_tier{tier}``      -> TOU demand peak proxy (kW) for a particular (year, month, TOU tier); indexed by nodes with TOU demand charges

    4. Parameters and Expressions
       - ``import_price[node, t]``                  -> node-specific import price ($/kWh) for each time period
       - ``grid_import_power_kw[node, t]``          -> grid import power proxy used only for demand charges: ``grid_import / time_step_hours`` (kW)

    5. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - ``grid_import[node, t]``                   -> utility block source-side contribution to the electricity balance in ``model.core``

    6. Contribution to the cost function - ``objective_contribution``
       - ``energy_import_cost``                     -> energy-import cost from ``import_price[node,t] * grid_import[node, t]``
       - ``nonTOU_Demand_Charge_Cost``              -> flat demand-charge cost when ``demand_charge_type`` includes flat / both
       - ``TOU_Demand_Charge_Cost``                 -> TOU demand-charge cost when ``demand_charge_type`` includes tou / both
       - ``fixed_usd``                              -> fixed customer-charge USD over the represented horizon from ``fixed_customer_charges_horizon_usd``

    7. Contribution to reporting - ``cost_non_optimizing_annual``
       - fixed customer-charge portion only; this is the usage-independent utility-fee term billed per node

    8. Constraints

       - ``flat_demand_charge_ub_m*_t*``           -> monthly: ``P_flat_m >= sum_n grid_import_power_kw[n,t]`` for all timesteps in month
       - ``tou_demand_charge_ub_m*_tier*_t*``      -> monthly-by-tier: ``P_tou_m{m}_tier{tier} >= sum_n grid_import_power_kw[n,t]`` for all timesteps in month mapped to that TOU tier
    """

    resolved = resolve_utility_inputs(model, data)
    if resolved is None:
        return None

    T = model.T
    NODES = list(model.NODES)
    prices_by_node = resolved.prices_by_node
    utility_tariff_by_node = resolved.utility_tariff_by_node
    has_any_demand_charges = resolved.has_any_demand_charges
    dt_hours_f = resolved.dt_hours_f
    datetimes = resolved.datetimes
    fixed_usd = resolved.fixed_usd
    time_indices = resolved.time_indices

    times_by_year_month = times_by_year_month_from_datetimes(datetimes, time_indices)
    year_months_in_run = sorted_year_month_keys(times_by_year_month)

    def block_rule(utility_block):
        utility_block.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals)
        if has_any_demand_charges:
            if dt_hours_f is None:
                raise RuntimeError("Internal error: dt_hours_f must be set when demand charges are active.")
            utility_block.grid_import_power_kw = pyo.Expression(
                NODES,
                T,
                rule=lambda m, node, t: m.grid_import[node, t] / dt_hours_f,
            )
        utility_block.electricity_source_term = pyo.Expression(
            NODES,
            T,
            rule=lambda m, node, t: m.grid_import[node, t],
        )
        utility_block.import_price = pyo.Param(
            NODES,
            T,
            initialize={
                (node, t): float(prices_by_node[node][t]) for node in NODES for t in T
            },
            within=pyo.Reals,
            mutable=True,
        )
        utility_block.energy_import_cost = sum(
            utility_block.import_price[node, t] * utility_block.grid_import[node, t]
            for node in NODES
            for t in T
        )
        flat_demand_charge_terms = []
        tou_demand_charge_terms = []

        for calendar_year, month_index in year_months_in_run:
            times_in_month = times_by_year_month[(calendar_year, month_index)]
            if not times_in_month:
                continue
            flat_nodes, flat_rate_by_node = flat_demand_nodes_and_rates_for_month(
                NODES, utility_tariff_by_node, month_index
            )
            if flat_nodes:
                flat_demand_peak_kw = pyo.Var(flat_nodes, within=pyo.NonNegativeReals)
                utility_block.add_component(
                    f"P_flat_y{calendar_year}_m{month_index}", flat_demand_peak_kw
                )
                month_time_index_set = pyo.Set(initialize=times_in_month, ordered=True)
                utility_block.add_component(
                    f"flat_demand_time_index_y{calendar_year}_m{month_index}", month_time_index_set
                )
                utility_block.add_component(
                    f"flat_demand_charge_ub_y{calendar_year}_m{month_index}",
                    pyo.Constraint(
                        flat_nodes,
                        month_time_index_set,
                        rule=lambda _blk, node, time_index: flat_demand_peak_kw[node]
                        >= _blk.grid_import_power_kw[node, time_index],
                    ),
                )
                flat_demand_charge_terms.append(
                    sum(flat_rate_by_node[node] * flat_demand_peak_kw[node] for node in flat_nodes)
                )

        for calendar_year, month_index in year_months_in_run:
            month_times = times_by_year_month[(calendar_year, month_index)]
            if not month_times:
                continue
            for group in tou_demand_tier_groups_for_month(
                month_times, datetimes, NODES, utility_tariff_by_node
            ):
                tier_index = group.tier_index
                tier_nodes = group.tier_nodes
                tou_demand_peak_kw = pyo.Var(tier_nodes, within=pyo.NonNegativeReals)
                utility_block.add_component(
                    f"P_tou_y{calendar_year}_m{month_index}_tier{tier_index}", tou_demand_peak_kw
                )
                tier_node_time_index = sorted(
                    (node, time_step)
                    for node in tier_nodes
                    for time_step in group.times_by_node[node]
                )
                tier_node_time_index_set = pyo.Set(dimen=2, initialize=tier_node_time_index, ordered=True)
                utility_block.add_component(
                    f"tou_demand_node_time_index_y{calendar_year}_m{month_index}_tier{tier_index}",
                    tier_node_time_index_set,
                )
                utility_block.add_component(
                    f"tou_demand_charge_ub_y{calendar_year}_m{month_index}_tier{tier_index}",
                    pyo.Constraint(
                        tier_node_time_index_set,
                        rule=lambda _blk, node, time_index: tou_demand_peak_kw[node]
                        >= _blk.grid_import_power_kw[node, time_index],
                    ),
                )
                tou_demand_charge_terms.append(
                    sum(group.rate_by_node[node] * tou_demand_peak_kw[node] for node in tier_nodes)
                )

        utility_block.nonTOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(flat_demand_charge_terms) if flat_demand_charge_terms else 0.0
        )
        utility_block.TOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(tou_demand_charge_terms) if tou_demand_charge_terms else 0.0
        )
        utility_block.objective_contribution = (
            utility_block.energy_import_cost
            + utility_block.nonTOU_Demand_Charge_Cost
            + utility_block.TOU_Demand_Charge_Cost
        )
        utility_block.cost_non_optimizing_annual = pyo.Expression(expr=fixed_usd)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


def register(model: Any, data: Any) -> pyo.Block | None:
    """Registry hook used by ``model.core``."""
    return add_utility_block(model, data)
