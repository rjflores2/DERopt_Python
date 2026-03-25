"""Electricity import/export utility block.

Provides grid import variable, energy cost from import_prices, and demand charges from
ParsedRate.demand_charges (flat and TOU). This is the generic grid/utility block; utility-specific
loaders normalize their tariffs into ParsedRate so this block does not branch on utility names.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo


def _tier_for_tou_demand_charge(dt, demand_charges: dict) -> int:
    """Return demand-charge tier index for datetime dt using 12×24 weekday/weekend schedules."""
    wd = demand_charges["demand_charge_weekdayschedule"]
    we = demand_charges["demand_charge_weekendschedule"]
    month = dt.month - 1
    hour = dt.hour
    is_weekend = dt.weekday() >= 5
    sched = we if is_weekend else wd
    n_tiers = len(demand_charges.get("demand_charge_ratestructure") or [])
    if month < len(sched) and hour < len(sched[month]):
        return min(sched[month][hour], max(0, n_tiers - 1))
    return 0


def _add_utility_block(model: Any, data: Any) -> pyo.Block | None:
    """
    Build and attach the grid / utility import block when energy prices and/or demand charges
    are available; otherwise return ``None``.

    1. Data and other inputs

       - ``model.T``                                -> time periods (from ``model.core``).
       - ``model.NODES``                            -> node keys (same as ``electricity_load_keys``).
       - ``model.import_prices``                    -> optional length-|T| energy price vector ($/kWh); may be
                                                       absent if only demand charges are modeled (then prices default to 0).
       - ``model.utility_rate``                     -> optional parsed tariff; ``demand_charges`` supplies flat/TOU demand logic.
       - ``data.timeseries["datetime"]``            -> timestamps for mapping hours to bill months / TOU tiers when present.

    2. Variables (Pyomo ``Var``)

       - ``grid_import[node, t]``                   -> grid energy purchased in period ``t`` (kWh).
       - ``P_flat_m{month}``                        -> flat-demand peak proxy (kW) per applicable bill month, when configured.
       - ``P_tou_tier{tier}``                       -> TOU demand peak proxy (kW) per rate tier, when configured.

    3. Parameters (Pyomo ``Param``)

       - ``import_price[t]``                        -> energy price ($/kWh) per period (zeros if only demand charges).

    4. Contribution to electricity sources — ``electricity_source_term[node, t]``

       - ``grid_import[node, t]``

    5. Contribution to electricity sinks — ``electricity_sink_term``

       - (none on this block; grid imports are supply-only here.)

    6. Contribution to the cost function — ``objective_contribution``

       - ``energy_import_cost``                     -> ``sum_t import_price[t] * sum_n grid_import[n,t]``.
       - ``nonTOU_Demand_Charge_Cost``              -> flat demand charge $ when ``demand_charge_type`` includes flat/both.
       - ``TOU_Demand_Charge_Cost``                 -> TOU demand charge $ when type includes tou/both.
       - ``objective_contribution``                 -> sum of energy + flat + TOU expressions above.
       - ``cost_existing_annual``                  -> ``0`` (placeholder for symmetry with tech blocks).

    7. Constraints

       - ``flat_demand_charge_ub_m*_t*``           -> ``P_flat`` for month ``>=`` sum of nodal ``grid_import`` in that month’s hours.
       - ``tou_demand_charge_ub_tier*_t*``         -> ``P_tier`` ``>=`` sum of nodal ``grid_import`` for hours mapped to that tier.
    """
    import_prices = getattr(model, "import_prices", None)
    utility_rate = getattr(model, "utility_rate", None)
    demand_charges = getattr(utility_rate, "demand_charges", None) if utility_rate is not None else None
    if import_prices is None and not (demand_charges and demand_charges.get("demand_charge_type")):
        return None

    T = model.T
    NODES = list(model.NODES)
    _T = list(T)
    datetimes = data.timeseries.get("datetime")
    if datetimes is None or len(datetimes) != len(_T):
        datetimes = [None] * len(_T)

    # If no energy prices but we have demand charges, use zero energy cost so demand charges still apply.
    prices = list(import_prices) if import_prices is not None else [0.0] * len(_T)

    def block_rule(b):
        b.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals)
        b.electricity_source_term = pyo.Expression(
            NODES, T,
            rule=lambda m, n, t: m.grid_import[n, t],
        )
        b.import_price = pyo.Param(T, initialize={t: prices[t] for t in T}, within=pyo.Reals, mutable=True)
        # Energy import cost: $/kWh * kWh = $ per period; sum over t and nodes.
        b.energy_import_cost = sum(
            b.import_price[t] * sum(b.grid_import[n, t] for n in NODES)
            for t in T
        )
        flat_demand_charge_terms = []
        tou_demand_charge_terms = []

        if demand_charges:
            # Flat demand charge: one peak variable per applicable month; P >= sum_n grid_import[n,t] for t in month.
            if demand_charges.get("demand_charge_type") in ("flat", "both"):
                flat_months = demand_charges.get("flat_demand_charge_applicable_months") or []
                flat_struct = demand_charges.get("flat_demand_charge_structure") or [[]]
                for mi in flat_months:
                    times_in_month = [
                        t for t in _T
                        if t < len(datetimes)
                        and datetimes[t] is not None
                        and datetimes[t].month - 1 == mi
                    ]
                    if not times_in_month:
                        continue
                    rate = 0.0
                    if flat_struct and flat_struct[0]:
                        tier0 = flat_struct[0][0] if isinstance(flat_struct[0], list) else flat_struct[0]
                        rate = float(tier0.get("rate", 0))
                    P_flat = pyo.Var(within=pyo.NonNegativeReals)
                    b.add_component(f"P_flat_m{mi}", P_flat)
                    for t in times_in_month:
                        b.add_component(
                            f"flat_demand_charge_ub_m{mi}_t{t}",
                            pyo.Constraint(expr=P_flat >= sum(b.grid_import[n, t] for n in NODES)),
                        )
                    flat_demand_charge_terms.append(rate * P_flat)

            # TOU demand charge: one peak variable per tier; P_tier >= sum_n grid_import[n,t] for t in tier.
            if demand_charges.get("demand_charge_type") in ("tou", "both"):
                drs = demand_charges.get("demand_charge_ratestructure") or []
                for ti, tier in enumerate(drs):
                    rate = 0.0
                    if isinstance(tier, list) and tier:
                        rate = float(tier[0].get("rate", 0))
                    elif isinstance(tier, dict):
                        rate = float(tier.get("rate", 0))
                    times_in_tier = [
                        t for t in _T
                        if t < len(datetimes)
                        and datetimes[t] is not None
                        and _tier_for_tou_demand_charge(datetimes[t], demand_charges) == ti
                    ]
                    if not times_in_tier:
                        continue
                    P_tier = pyo.Var(within=pyo.NonNegativeReals)
                    b.add_component(f"P_tou_tier{ti}", P_tier)
                    for t in times_in_tier:
                        b.add_component(
                            f"tou_demand_charge_ub_tier{ti}_t{t}",
                            pyo.Constraint(expr=P_tier >= sum(b.grid_import[n, t] for n in NODES)),
                        )
                    tou_demand_charge_terms.append(rate * P_tier)

        # Expose demand-charge components separately for reporting; names match common rate language.
        b.nonTOU_Demand_Charge_Cost = pyo.Expression(expr=sum(flat_demand_charge_terms) if flat_demand_charge_terms else 0.0)
        b.TOU_Demand_Charge_Cost = pyo.Expression(expr=sum(tou_demand_charge_terms) if tou_demand_charge_terms else 0.0)
        b.objective_contribution = b.energy_import_cost + b.nonTOU_Demand_Charge_Cost + b.TOU_Demand_Charge_Cost
        b.cost_existing_annual = pyo.Expression(expr=0.0)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


def register(model: Any, data: Any) -> pyo.Block | None:
    """
    Registry hook used by ``model.core``: call ``_add_utility_block`` (returns a block or ``None``).
    """
    return _add_utility_block(model, data)

