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

    Assumption: each ``node`` is one customer/meter for utility billing.

    See module docstring in ``utilities.electricity_import_export`` package for component list.
    """
    resolved = resolve_utility_inputs(model, data)
    if resolved is None:
        return None

    T = model.T
    NODES = list(model.NODES)
    prices_by_node = resolved.prices_by_node
    rates_by_node = resolved.rates_by_node
    has_any_demand_charges = resolved.has_any_demand_charges
    dt_hours_f = resolved.dt_hours_f
    datetimes = resolved.datetimes
    fixed_usd = resolved.fixed_usd
    time_indices = resolved.time_indices

    times_by_year_month = times_by_year_month_from_datetimes(datetimes, time_indices)
    year_months_in_run = sorted_year_month_keys(times_by_year_month)

    def block_rule(b):
        b.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals)
        if has_any_demand_charges:
            if dt_hours_f is None:
                raise RuntimeError("Internal error: dt_hours_f must be set when demand charges are active.")
            b.grid_import_power_kw = pyo.Expression(
                NODES,
                T,
                rule=lambda m, n, t: m.grid_import[n, t] / dt_hours_f,
            )
        b.electricity_source_term = pyo.Expression(
            NODES,
            T,
            rule=lambda m, n, t: m.grid_import[n, t],
        )
        b.import_price = pyo.Param(
            NODES,
            T,
            initialize={(n, t): float(prices_by_node[n][t]) for n in NODES for t in T},
            within=pyo.Reals,
            mutable=True,
        )
        b.energy_import_cost = sum(b.import_price[n, t] * b.grid_import[n, t] for n in NODES for t in T)
        flat_demand_charge_terms = []
        tou_demand_charge_terms = []

        for yy, month_index in year_months_in_run:
            times_in_month = times_by_year_month[(yy, month_index)]
            if not times_in_month:
                continue
            flat_nodes, flat_rate_by_node = flat_demand_nodes_and_rates_for_month(
                NODES, rates_by_node, month_index
            )
            if flat_nodes:
                P_flat = pyo.Var(flat_nodes, within=pyo.NonNegativeReals)
                b.add_component(f"P_flat_y{yy}_m{month_index}", P_flat)
                month_time_index_set = pyo.Set(initialize=times_in_month, ordered=True)
                b.add_component(f"flat_demand_time_index_y{yy}_m{month_index}", month_time_index_set)
                b.add_component(
                    f"flat_demand_charge_ub_y{yy}_m{month_index}",
                    pyo.Constraint(
                        flat_nodes,
                        month_time_index_set,
                        rule=lambda _b, n, time_index: P_flat[n] >= _b.grid_import_power_kw[n, time_index],
                    ),
                )
                flat_demand_charge_terms.append(sum(flat_rate_by_node[n] * P_flat[n] for n in flat_nodes))

        for yy, month_index in year_months_in_run:
            month_times = times_by_year_month[(yy, month_index)]
            if not month_times:
                continue
            for group in tou_demand_tier_groups_for_month(
                month_times, datetimes, NODES, rates_by_node
            ):
                ti = group.tier_index
                tier_nodes = group.tier_nodes
                P_tou = pyo.Var(tier_nodes, within=pyo.NonNegativeReals)
                b.add_component(f"P_tou_y{yy}_m{month_index}_tier{ti}", P_tou)
                tier_node_time_index = sorted((n, t) for n in tier_nodes for t in group.times_by_node[n])
                tier_node_time_index_set = pyo.Set(dimen=2, initialize=tier_node_time_index, ordered=True)
                b.add_component(
                    f"tou_demand_node_time_index_y{yy}_m{month_index}_tier{ti}",
                    tier_node_time_index_set,
                )
                b.add_component(
                    f"tou_demand_charge_ub_y{yy}_m{month_index}_tier{ti}",
                    pyo.Constraint(
                        tier_node_time_index_set,
                        rule=lambda _b, n, time_index: P_tou[n] >= _b.grid_import_power_kw[n, time_index],
                    ),
                )
                tou_demand_charge_terms.append(
                    sum(group.rate_by_node[n] * P_tou[n] for n in tier_nodes)
                )

        b.nonTOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(flat_demand_charge_terms) if flat_demand_charge_terms else 0.0
        )
        b.TOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(tou_demand_charge_terms) if tou_demand_charge_terms else 0.0
        )
        b.objective_contribution = (
            b.energy_import_cost
            + b.nonTOU_Demand_Charge_Cost
            + b.TOU_Demand_Charge_Cost
        )
        b.cost_non_optimizing_annual = pyo.Expression(expr=fixed_usd)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


def register(model: Any, data: Any) -> pyo.Block | None:
    """Registry hook used by ``model.core``."""
    return add_utility_block(model, data)
