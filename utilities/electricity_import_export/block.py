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
       - ``model.import_prices_by_node``            -> optional per-node import price vectors ($/kWh)
       - ``model.utility_rate_by_node``             -> optional per-node parsed tariff objects
       - ``data.static["time_step_hours"]``         -> required when any demand charges are active (used to convert kWh/period to kW)
       - ``data.timeseries["datetime"]``            -> required when any demand charges are active (used to map timesteps into bill months and TOU tiers); also used to prorate fixed customer charges across the represented horizon

    2. Sets (Pyomo ``Set``)
       - ``model.T``                                -> time index used by the utility block
       - ``model.NODES``                            -> node index used by the utility block
       - ``flat_peak_index``                        -> ``(year, month, node)`` tuples that receive a flat-demand peak variable
       - ``flat_ub_index``                          -> ``(year, month, node, t)`` tuples used as the flat demand-charge upper-bound constraint index (``t`` restricted to that month)
       - ``tou_peak_index``                         -> ``(year, month, tier, node)`` tuples that receive a TOU demand peak variable
       - ``tou_ub_index``                           -> ``(year, month, tier, node, t)`` tuples used as the TOU demand-charge upper-bound constraint index (``t`` restricted to that tier's hours in that month for that node)

    3. Variables (Pyomo ``Var``)
       - ``grid_import[node, t]``                   -> grid energy imported at each node and time period (kWh/period)
       - ``P_flat[year, month, node]``              -> flat-demand peak proxy (kW) for one bill month and node; enforces a monthly peak envelope
       - ``P_tou[year, month, tier, node]``         -> TOU demand peak proxy (kW) for one bill month, TOU tier, and node; enforces a per-month-per-tier peak envelope

    4. Parameters and Expressions
       - ``import_price[node, t]``                  -> node-specific import price ($/kWh) for each time period
       - ``flat_demand_rate[year, month, node]``    -> flat $/kW rate applied to ``P_flat[year, month, node]``
       - ``tou_demand_rate[year, month, tier, node]`` -> TOU $/kW rate applied to ``P_tou[year, month, tier, node]``
       - ``grid_import_power_kw[node, t]``          -> grid import power proxy used only for demand charges: ``grid_import / time_step_hours`` (kW)

    5. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - ``grid_import[node, t]``                   -> utility block source-side contribution to the electricity balance in ``model.core``

    6. Contribution to the cost function - ``objective_contribution``
       - ``energy_import_cost``                     -> energy-import cost from ``import_price[node,t] * grid_import[node, t]``
       - ``nonTOU_Demand_Charge_Cost``              -> sum over ``flat_peak_index`` of ``flat_demand_rate * P_flat``
       - ``TOU_Demand_Charge_Cost``                 -> sum over ``tou_peak_index`` of ``tou_demand_rate * P_tou``
       - ``fixed_usd``                              -> fixed customer-charge USD over the represented horizon from ``fixed_customer_charges_horizon_usd``

    7. Contribution to reporting - ``cost_non_optimizing_annual``
       - fixed customer-charge portion only; this is the usage-independent utility-fee term billed per node

    8. Constraints
       - ``flat_demand_charge_ub[year, month, node, t]``          -> ``P_flat[year, month, node] >= grid_import_power_kw[node, t]`` for each timestep ``t`` in that ``(year, month)``
       - ``tou_demand_charge_ub[year, month, tier, node, t]``     -> ``P_tou[year, month, tier, node] >= grid_import_power_kw[node, t]`` for each ``t`` in that ``(year, month, tier, node)``'s hours
    """

    resolved = resolve_utility_inputs(model, data)
    if resolved is None:
        return None

    T = model.T  # Time from the model
    NODES = list(model.NODES)  # Nodes from the model
    prices_by_node = resolved.prices_by_node  # Energy charge prices by node
    utility_tariff_by_node = resolved.utility_tariff_by_node  # Utility tariff by node
    has_any_demand_charges = resolved.has_any_demand_charges  # Whether there are any demand charges
    dt_hours_f = resolved.dt_hours_f  # Time step hours from the model
    datetimes = resolved.datetimes  # Datetimes from the model
    fixed_usd = resolved.fixed_usd  # Fixed customer charges in USD from the model
    time_indices = resolved.time_indices  # Time indices from the model

    # --- Build the demand-charge index tuples and rate dicts in plain Python. ---
    # The (year, month) boundary lives in the index tuples themselves: each P_flat / P_tou
    # element is only ever upper-bounded by timesteps drawn from its own (year, month)
    # bucket (and, for TOU, its own tier hours), preserving per-month billing semantics.
    flat_peak_keys: list[tuple[int, int, str]] = []
    flat_rate_by_key: dict[tuple[int, int, str], float] = {}
    flat_ub_keys: list[tuple[int, int, str, int]] = []

    tou_peak_keys: list[tuple[int, int, int, str]] = []
    tou_rate_by_key: dict[tuple[int, int, int, str], float] = {}
    tou_ub_keys: list[tuple[int, int, int, str, int]] = []

    if has_any_demand_charges:
        times_by_year_month = times_by_year_month_from_datetimes(datetimes, time_indices)
        year_months_in_run = sorted_year_month_keys(times_by_year_month)

        for calendar_year, month_index in year_months_in_run:
            times_in_month = times_by_year_month[(calendar_year, month_index)]
            if not times_in_month:
                continue

            # Flat demand: one peak per (year, month, node) with a positive $/kW rate.
            flat_nodes, flat_rate_by_node = flat_demand_nodes_and_rates_for_month(
                NODES, utility_tariff_by_node, month_index
            )
            for node in flat_nodes:
                rate = float(flat_rate_by_node.get(node, 0.0))
                # OpenEI can list flat demand with $/kW == 0 (no charge for that month/structure) — skip vars/constraints.
                if rate <= 0.0:
                    continue
                key = (calendar_year, month_index, node)
                flat_peak_keys.append(key)
                flat_rate_by_key[key] = rate
                for t in times_in_month:
                    flat_ub_keys.append((calendar_year, month_index, node, t))

            # TOU demand: one peak per (year, month, tier, node); hours are per-node within the tier.
            for group in tou_demand_tier_groups_for_month(
                times_in_month, datetimes, NODES, utility_tariff_by_node
            ):
                tier_index = group.tier_index
                for node in group.tier_nodes:
                    rate = float(group.rate_by_node.get(node, 0.0))
                    # OpenEI often uses $/kW == 0 for a tier or period meaning no demand charge there — skip vars/constraints.
                    if rate <= 0.0:
                        continue
                    key = (calendar_year, month_index, tier_index, node)
                    tou_peak_keys.append(key)
                    tou_rate_by_key[key] = rate
                    for t in group.times_by_node[node]:
                        tou_ub_keys.append((calendar_year, month_index, tier_index, node, t))

    def block_rule(utility_block):  # Pyomo block for utility import/export/possibly demand charges
        # --- Grid import (kWh/period): shared by energy charges, demand peaks, and the electricity balance ---
        utility_block.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals)

        # --- Demand charges: average kW over each period from kWh/period (only if any node has demand charges) ---
        if has_any_demand_charges:
            if dt_hours_f is None:
                raise RuntimeError(
                    "Internal error: dt_hours_f must be set when demand charges are active."
                )
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

        # --- Energy charges: $/kWh × kWh imported (per node, per time) ---
        utility_block.import_price = pyo.Param(
            NODES,
            T,
            initialize={
                (node, t): float(prices_by_node[node][t]) for node in NODES for t in T
            },
            within=pyo.Reals,
            mutable=False,
        )
        utility_block.energy_import_cost = pyo.Expression(
            expr=sum(
                utility_block.import_price[node, t] * utility_block.grid_import[node, t]
                for node in NODES
                for t in T
            )
        )

        ###########################
        ### Flat demand charges ###
        ###########################
        utility_block.flat_peak_index = pyo.Set(
            dimen=3, initialize=flat_peak_keys, ordered=True
        )
        utility_block.flat_ub_index = pyo.Set(
            dimen=4, initialize=flat_ub_keys, ordered=True
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

        def _flat_ub_rule(b, year, month, node, t):
            # Monthly envelope: the (year, month) peak must cover every timestep in that month.
            return b.P_flat[year, month, node] >= b.grid_import_power_kw[node, t]

        utility_block.flat_demand_charge_ub = pyo.Constraint(
            utility_block.flat_ub_index, rule=_flat_ub_rule
        )

        utility_block.nonTOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(
                utility_block.flat_demand_rate[key] * utility_block.P_flat[key]
                for key in utility_block.flat_peak_index
            )
        )

        ##########################
        ### TOU demand charges ###
        ##########################
        utility_block.tou_peak_index = pyo.Set(
            dimen=4, initialize=tou_peak_keys, ordered=True
        )
        utility_block.tou_ub_index = pyo.Set(
            dimen=5, initialize=tou_ub_keys, ordered=True
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

        def _tou_ub_rule(b, year, month, tier, node, t):
            # Monthly-by-tier envelope: peak only sees this tier's hours within this (year, month).
            return b.P_tou[year, month, tier, node] >= b.grid_import_power_kw[node, t]

        utility_block.tou_demand_charge_ub = pyo.Constraint(
            utility_block.tou_ub_index, rule=_tou_ub_rule
        )

        utility_block.TOU_Demand_Charge_Cost = pyo.Expression(
            expr=sum(
                utility_block.tou_demand_rate[key] * utility_block.P_tou[key]
                for key in utility_block.tou_peak_index
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
