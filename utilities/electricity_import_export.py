"""Electricity import/export utility block.

Provides grid import variable, node-specific energy cost, and node-specific demand charges from
ParsedRate.demand_charges (flat and TOU). This is the generic grid/utility block; utility-specific
loaders normalize their tariffs into ParsedRate so this block does not branch on utility names.

Layout: ``_add_utility_block`` (main Pyomo builder) first, then small helpers, then ``register``.
"""

from __future__ import annotations

import re
from typing import Any

import pyomo.environ as pyo

from data_loading.loaders.utility_rates.customer_charge_horizon import (
    fixed_customer_charges_horizon_usd,
)


def _add_utility_block(model: Any, data: Any) -> pyo.Block | None:
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
    import_prices_by_node = getattr(model, "import_prices_by_node", None)
    utility_rate_by_node = getattr(model, "utility_rate_by_node", None)

    T = model.T
    NODES = list(model.NODES)
    _T = list(T)
    datetimes = data.timeseries.get("datetime")
    if datetimes is None or len(datetimes) != len(_T):
        datetimes = [None] * len(_T)

    # Resolve node-scoped utility inputs.
    prices_by_node: dict[str, list[float]] = {}
    rates_by_node: dict[str, Any | None] = {}
    _zero_prices = [0.0] * len(_T)
    for n in NODES:
        p = None
        if isinstance(import_prices_by_node, dict):
            p = import_prices_by_node.get(n)
        # Avoid copying shared price vectors (same list may back many nodes on one tariff).
        if p is not None:
            prices_by_node[n] = p if len(p) == len(_T) else list(p)
        else:
            prices_by_node[n] = _zero_prices

        r = None
        if isinstance(utility_rate_by_node, dict):
            r = utility_rate_by_node.get(n)
        rates_by_node[n] = r

    # Check if any node has demand charges.
    def _demand_type_for_node(node: str) -> str | None:
        demand_charges = (
            getattr(rates_by_node[node], "demand_charges", None) if rates_by_node[node] is not None else None
        )
        demand_charge_type = demand_charges.get("demand_charge_type") if isinstance(demand_charges, dict) else None
        if demand_charge_type in ("flat", "tou", "both"):
            return demand_charge_type
        return None

    has_any_demand_charges = any(_demand_type_for_node(n) is not None for n in NODES)
    dt_hours_f: float | None = None
    # Time-step-dependent components require explicit time_step_hours.
    if has_any_demand_charges:
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
        if any(dt is None for dt in datetimes):
            raise ValueError(
                "Demand charges are present but data.timeseries['datetime'] is missing or misaligned with the run horizon. "
                "Demand-charge month/tier mapping requires one valid datetime per period."
            )

    # Fixed customer charges are reporting-only and billed per node (node=customer assumption).
    fixed_usd = sum(
        fixed_customer_charges_horizon_usd(
            getattr(rates_by_node[n], "customer_fixed_charges", None) if rates_by_node[n] is not None else None,
            datetimes,
        )
        for n in NODES
    )

    has_node_energy_prices = isinstance(import_prices_by_node, dict) and bool(import_prices_by_node)
    has_energy_or_demand = has_node_energy_prices or has_any_demand_charges
    
    # Checking if utility block should get built
    if not has_energy_or_demand and fixed_usd == 0:
        return None

    #Sanitize node names for Pyomo block names.
    def _tok(x: str) -> str:
        return re.sub(r"[^0-9A-Za-z_]+", "_", x)

    def block_rule(b): #Pyomo block 
        # Grid import variable (kWh/period)
        b.grid_import = pyo.Var(NODES, T, within=pyo.NonNegativeReals) # Grid import variable (kWh/period)
        # Power proxy for demand charges: kWh/period ÷ (h/period) = kW.
        if has_any_demand_charges:
            if dt_hours_f is None:
                raise RuntimeError("Internal error: dt_hours_f must be set when demand charges are active.")
            b.grid_import_power_kw = pyo.Expression( # Power proxy for demand charges: kWh/period ÷ (h/period) = kW.
                NODES,
                T,
                rule=lambda m, n, t: m.grid_import[n, t] / dt_hours_f,
            )
            # Electricity source term for core electricity balance.
        b.electricity_source_term = pyo.Expression( # Contribution to electricity sources - ``electricity_source_term[node, t]``
            NODES, T,
            rule=lambda m, n, t: m.grid_import[n, t],
        )
        # Node-specific import price ($/kWh) for each time period
        b.import_price = pyo.Param( 
            NODES,
            T,
            initialize={(n, t): float(prices_by_node[n][t]) for n in NODES for t in T},
            within=pyo.Reals,
            mutable=True,
        )
        # Energy import cost: node-specific $/kWh * kWh = $ per period.
        b.energy_import_cost = sum(b.import_price[n, t] * b.grid_import[n, t] for n in NODES for t in T)
        flat_demand_charge_terms = []
        tou_demand_charge_terms = []

        # Pre-index timesteps by (year, month_index) once and reuse for flat + TOU loops.
        times_by_year_month: dict[tuple[int, int], list[int]] = {}
        for t in _T:
            dt = datetimes[t]
            if dt is None:
                continue
            key = (dt.year, dt.month - 1)
            times_by_year_month.setdefault(key, []).append(t)
        year_months_in_run = sorted(times_by_year_month.keys())

        # Flat demand charge: per-node, only for nodes whose tariff has flat (or both).
        for yy, month_index in year_months_in_run:
            times_in_month = times_by_year_month[(yy, month_index)]
            if not times_in_month:
                continue
            flat_nodes: list[str] = []
            flat_rate_by_node: dict[str, float] = {}
            for n in NODES:
                utility_rate_for_node = rates_by_node[n]
                demand_charges = (
                    getattr(utility_rate_for_node, "demand_charges", None)
                    if utility_rate_for_node is not None
                    else None
                )
                if not demand_charges or demand_charges.get("demand_charge_type") not in ("flat", "both"):
                    continue
                applicable = set(demand_charges.get("flat_demand_charge_applicable_months") or [])
                if applicable and month_index not in applicable:
                    continue
                flat_struct = demand_charges.get("flat_demand_charge_structure") or [[]]
                flat_month_map = demand_charges.get("flat_demand_charge_months") or []
                struct_idx = 0
                if month_index < len(flat_month_map):
                    try:
                        struct_idx = int(flat_month_map[month_index])
                    except (TypeError, ValueError) as e:
                        raise ValueError(
                            f"Node {n!r}: flat_demand_charge_months[{month_index}] must be an int structure index; got {flat_month_map[month_index]!r}"
                        ) from e
                if not isinstance(flat_struct, list) or not flat_struct:
                    raise ValueError(f"Node {n!r}: flat_demand_charge_structure must be a non-empty list")
                if struct_idx < 0 or struct_idx >= len(flat_struct):
                    raise ValueError(
                        f"Node {n!r}: flat_demand_charge_months[{month_index}] selects structure index {struct_idx} out of range "
                        f"for flat_demand_charge_structure (len={len(flat_struct)})"
                    )
                flat_nodes.append(n)
                flat_rate_by_node[n] = _rate_from_urdb_structure(flat_struct[struct_idx])
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

        # TOU demand charge: per-node, only for nodes whose tariff has tou (or both).
        for yy, month_index in year_months_in_run:
            month_times = times_by_year_month[(yy, month_index)]
            if not month_times:
                continue
            # Group node/timestep by TOU tier.
            times_by_tier_node: dict[int, dict[str, list[int]]] = {}
            rate_by_tier_node: dict[tuple[int, str], float] = {}
            for n in NODES:
                utility_rate_for_node = rates_by_node[n]
                demand_charges = (
                    getattr(utility_rate_for_node, "demand_charges", None)
                    if utility_rate_for_node is not None
                    else None
                )
                if not demand_charges or demand_charges.get("demand_charge_type") not in ("tou", "both"):
                    continue
                drs = demand_charges.get("demand_charge_ratestructure") or []
                for t in month_times:
                    ti = _tier_for_tou_demand_charge(datetimes[t], demand_charges)
                    tier = drs[ti] if ti < len(drs) else {}
                    rate_by_tier_node[(ti, n)] = _rate_from_urdb_structure(tier)
                    times_by_tier_node.setdefault(ti, {}).setdefault(n, []).append(t)
            for ti, by_node in sorted(times_by_tier_node.items()):
                tier_nodes = sorted(by_node.keys())
                if not tier_nodes:
                    continue
                P_tou = pyo.Var(tier_nodes, within=pyo.NonNegativeReals)
                b.add_component(f"P_tou_y{yy}_m{month_index}_tier{ti}", P_tou)
                tier_node_time_index = sorted((n, t) for n in tier_nodes for t in by_node[n])
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
                tou_demand_charge_terms.append(sum(rate_by_tier_node[(ti, n)] * P_tou[n] for n in tier_nodes))

        # Expose demand-charge components separately for reporting; names match common rate language.
        b.nonTOU_Demand_Charge_Cost = pyo.Expression(expr=sum(flat_demand_charge_terms) if flat_demand_charge_terms else 0.0)
        b.TOU_Demand_Charge_Cost = pyo.Expression(expr=sum(tou_demand_charge_terms) if tou_demand_charge_terms else 0.0)
        # Objective includes only decision-relevant utility costs.
        b.objective_contribution = (
            b.energy_import_cost
            + b.nonTOU_Demand_Charge_Cost
            + b.TOU_Demand_Charge_Cost
        )
        # Reporting-only fixed utility customer charges (constant wrt decision variables).
        b.cost_non_optimizing_annual = pyo.Expression(expr=fixed_usd)

    model.utility = pyo.Block(rule=block_rule)
    return model.utility


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

