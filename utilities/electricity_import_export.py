"""Electricity import/export utility block.

Provides grid import variable, energy cost from import_prices, and demand charges from
ParsedRate.demand_charges (flat and TOU). This is the generic grid/utility block; utility-specific
loaders normalize their tariffs into ParsedRate so this block does not branch on utility names.

Layout: ``_add_utility_block`` (main Pyomo builder) first, then small helpers, then ``register``.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.loaders.utility_rates.customer_charge_horizon import (
    fixed_customer_charges_horizon_usd,
)

def _rate_from_urdb_structure(struct: Any) -> float:
    """Best-effort extract of ``rate`` from OpenEI/URDB (possibly nested) structures.

    Common shapes:
    - [[{"rate": 12.3}]]  (tiered lists)
    - [{"rate": 12.3}]
    - {"rate": 12.3}
    """
    if struct is None:
        return 0.0
    if isinstance(struct, dict):
        return float(struct.get("rate", 0) or 0.0)
    if isinstance(struct, list) and struct:
        first = struct[0]
        return _rate_from_urdb_structure(first)
    return 0.0


def _add_utility_block(model: Any, data: Any) -> pyo.Block | None:
    """
    Build and attach the grid / utility import block when energy prices, demand charges,
    and/or fixed customer charges apply; otherwise return ``None``.

    1. Data and other inputs
       - ``model.import_prices``                    -> optional length-|T| import price vector ($/kWh); if absent and the block is still built, prices default to 0
       - ``model.utility_rate``                     -> optional parsed tariff object; may provide ``demand_charges`` and ``customer_fixed_charges``
       - ``data.static["time_step_hours"]``         -> required when any demand charges are active (used to convert kWh/period to kW)
       - ``data.timeseries["datetime"]``            -> required when any demand charges are active (used to map timesteps into bill months and TOU tiers); also used to prorate fixed customer charges across the represented horizon

    2. Sets (Pyomo ``Set``)
       - ``model.T``                                -> time index used by the utility block
       - ``model.NODES``                            -> node index used by the utility block

    3. Variables (Pyomo ``Var``)
       - ``grid_import[node, t]``                   -> grid energy imported at each node and time period (kWh/period)
       - ``P_flat_m{month}``                        -> flat-demand peak proxy for an applicable bill month; only created when flat demand charges are active
       - ``P_tou_m{month}_tier{tier}``              -> TOU demand peak proxy (kW) for a particular (bill month, TOU demand tier); only created when that tier actually occurs in that month over the modeled horizon

    4. Parameters and Expressions
       - ``import_price[t]``                        -> import price ($/kWh) for each time period
       - ``grid_import_power_kw[node, t]``          -> grid import power proxy used only for demand charges: ``grid_import / time_step_hours`` (kW)

    5. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - ``grid_import[node, t]``                   -> utility block source-side contribution to the electricity balance in ``model.core``

    6. Contribution to the cost function - ``objective_contribution``
       - ``energy_import_cost``                     -> energy-import cost from ``import_price[t] * grid_import[node, t]``
       - ``nonTOU_Demand_Charge_Cost``              -> flat demand-charge cost when ``demand_charge_type`` includes flat / both
       - ``TOU_Demand_Charge_Cost``                 -> TOU demand-charge cost when ``demand_charge_type`` includes tou / both
       - ``fixed_usd``                              -> fixed customer-charge USD over the represented horizon from ``fixed_customer_charges_horizon_usd``

    7. Contribution to reporting - ``cost_existing_annual``
       - fixed customer-charge portion only; this is the usage-independent utility-fee term

    8. Constraints

       - ``flat_demand_charge_ub_m*_t*``           -> monthly: ``P_flat_m >= sum_n grid_import_power_kw[n,t]`` for all timesteps in month
       - ``tou_demand_charge_ub_m*_tier*_t*``      -> monthly-by-tier: ``P_tou_m{m}_tier{tier} >= sum_n grid_import_power_kw[n,t]`` for all timesteps in month mapped to that TOU tier
    """
    import_prices = getattr(model, "import_prices", None) # Pulls utility rate info that was attached to model.core
    utility_rate = getattr(model, "utility_rate", None)
    demand_charges = getattr(utility_rate, "demand_charges", None) if utility_rate is not None else None

    # Time-step-dependent components require an explicit time step. For example, demand charges
    # are intended to proxy peak kW, but this model's grid_import decision variables are in kWh
    # per period; without time_step_hours we cannot interpret kWh/period as kW.
    if demand_charges and demand_charges.get("demand_charge_type"):
        dt_hours = (getattr(data, "static", {}) or {}).get("time_step_hours")
        if dt_hours is None:
            raise ValueError(
                "Demand charges are present but data.static['time_step_hours'] is missing. "
                "Time-step-dependent components require an explicit time_step_hours."
            )
        try:
            dt_hours_f = float(dt_hours)
        except (TypeError, ValueError) as e:
            raise ValueError(
                "Demand charges are present but data.static['time_step_hours'] is not numeric "
                f"(got {dt_hours!r})."
            ) from e
        if dt_hours_f <= 0:
            raise ValueError(
                "Demand charges are present but data.static['time_step_hours'] must be > 0 "
                f"(got {dt_hours_f!r})."
            )

    T = model.T # time index from model.core
    NODES = list(model.NODES) # node index from model.core
    _T = list(T) # turning T from model.core into a list - the python list is more straightforward to use for indexing
    datetimes = data.timeseries.get("datetime") # pulls the datetime index from the data container
    if datetimes is None or len(datetimes) != len(_T):
        datetimes = [None] * len(_T) # if the datetime index is not present, we default to None for all time periods
    if demand_charges and demand_charges.get("demand_charge_type"):
        if any(dt is None for dt in datetimes):
            raise ValueError(
                "Demand charges are present but data.timeseries['datetime'] is missing or misaligned with the run horizon. "
                "Demand-charge month/tier mapping requires one valid datetime per period."
            )

    fc = getattr(utility_rate, "customer_fixed_charges", None) if utility_rate is not None else None
    fixed_usd = fixed_customer_charges_horizon_usd(fc, datetimes)

    has_energy_or_demand = import_prices is not None or (
        demand_charges and demand_charges.get("demand_charge_type")
    )
    if not has_energy_or_demand and fixed_usd == 0:
        return None

    # If no energy prices but we have demand charges or fixed charges, use zero energy cost per period.
    prices = list(import_prices) if import_prices is not None else [0.0] * len(_T)

    def block_rule(b):
        b.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals)
        # Power proxy for demand charges: kWh/period ÷ (h/period) = kW.
        if demand_charges and demand_charges.get("demand_charge_type"):
            b.grid_import_power_kw = pyo.Expression(
                NODES,
                T,
                rule=lambda m, n, t: m.grid_import[n, t] / dt_hours_f,
            )
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
                # Only create month variables for months that actually appear in the horizon.
                months_in_run = sorted({dt.month - 1 for dt in datetimes if dt is not None})
                applicable = set(demand_charges.get("flat_demand_charge_applicable_months") or [])
                flat_struct = demand_charges.get("flat_demand_charge_structure") or [[]]
                flat_month_map = demand_charges.get("flat_demand_charge_months") or []
                for mi in months_in_run:
                    if applicable and mi not in applicable:
                        continue
                    times_in_month = [
                        t for t in _T
                        if t < len(datetimes)
                        and datetimes[t] is not None
                        and datetimes[t].month - 1 == mi
                    ]
                    if not times_in_month:
                        continue
                    # URDB/OpenEI often uses flatdemandmonths to map each month -> a structure index
                    # (e.g. winter=0, summer=1) rather than providing 12 separate structures.
                    struct_idx = 0
                    if mi < len(flat_month_map):
                        try:
                            struct_idx = int(flat_month_map[mi])
                        except (TypeError, ValueError) as e:
                            raise ValueError(
                                f"flat_demand_charge_months[{mi}] must be an int structure index; got {flat_month_map[mi]!r}"
                            ) from e
                    if not isinstance(flat_struct, list) or not flat_struct:
                        raise ValueError("flat_demand_charge_structure must be a non-empty list")
                    if struct_idx < 0 or struct_idx >= len(flat_struct):
                        raise ValueError(
                            f"flat_demand_charge_months[{mi}] selects structure index {struct_idx} out of range "
                            f"for flat_demand_charge_structure (len={len(flat_struct)})"
                        )
                    rate = _rate_from_urdb_structure(flat_struct[struct_idx])
                    P_flat = pyo.Var(within=pyo.NonNegativeReals)
                    b.add_component(f"P_flat_m{mi}", P_flat)
                    for t in times_in_month:
                        b.add_component(
                            f"flat_demand_charge_ub_m{mi}_t{t}",
                            pyo.Constraint(expr=P_flat >= sum(b.grid_import_power_kw[n, t] for n in NODES)),
                        )
                    flat_demand_charge_terms.append(rate * P_flat)

            # TOU demand charge: one peak variable per (month, tier); P >= sum_n grid_import_power_kw[n,t] for t in that month mapped to tier.
            if demand_charges.get("demand_charge_type") in ("tou", "both"):
                drs = demand_charges.get("demand_charge_ratestructure") or []
                # Map each timestep to (month, tier) using the existing OpenEI schedule fields.
                times_by_month_tier: dict[tuple[int, int], list[int]] = {}
                for t in _T:
                    dt = datetimes[t]
                    if dt is None:
                        continue
                    mi = dt.month - 1
                    ti = _tier_for_tou_demand_charge(dt, demand_charges)
                    times_by_month_tier.setdefault((mi, ti), []).append(t)

                for (mi, ti), times in sorted(times_by_month_tier.items()):
                    if not times:
                        continue
                    tier = drs[ti] if ti < len(drs) else {}
                    rate = 0.0
                    if isinstance(tier, list) and tier:
                        rate = float(tier[0].get("rate", 0) if isinstance(tier[0], dict) else 0.0)
                    elif isinstance(tier, dict):
                        rate = float(tier.get("rate", 0) or 0.0)
                    P_mt = pyo.Var(within=pyo.NonNegativeReals)
                    b.add_component(f"P_tou_m{mi}_tier{ti}", P_mt)
                    for t in times:
                        b.add_component(
                            f"tou_demand_charge_ub_m{mi}_tier{ti}_t{t}",
                            pyo.Constraint(expr=P_mt >= sum(b.grid_import_power_kw[n, t] for n in NODES)),
                        )
                    tou_demand_charge_terms.append(rate * P_mt)

        # Expose demand-charge components separately for reporting; names match common rate language.
        b.nonTOU_Demand_Charge_Cost = pyo.Expression(expr=sum(flat_demand_charge_terms) if flat_demand_charge_terms else 0.0)
        b.TOU_Demand_Charge_Cost = pyo.Expression(expr=sum(tou_demand_charge_terms) if tou_demand_charge_terms else 0.0)
        b.objective_contribution = (
            b.energy_import_cost
            + b.nonTOU_Demand_Charge_Cost
            + b.TOU_Demand_Charge_Cost
            + fixed_usd
        )
        b.cost_existing_annual = pyo.Expression(expr=fixed_usd)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


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


def register(model: Any, data: Any) -> pyo.Block | None:
    """
    Registry hook used by ``model.core``: call ``_add_utility_block`` (returns a block or ``None``).
    """
    return _add_utility_block(model, data)

