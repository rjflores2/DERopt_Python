"""Resolve and validate utility-block inputs from ``model`` and ``data`` (no Pyomo)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from data_loading.loaders.utility_rates.customer_charge_horizon import (
    fixed_customer_charges_horizon_usd,
)
from data_loading.schemas import DataContainer


@dataclass(frozen=True)
class ResolvedUtilityInputs:
    """Everything needed to build ``model.utility`` except Pyomo objects."""

    prices_by_node: dict[str, list[float]]
    utility_tariff_by_node: dict[str, Any | None]
    has_any_demand_charges: bool
    dt_hours_f: float | None
    datetimes: list[Any | None]
    fixed_usd: float
    has_node_energy_prices: bool
    time_indices: list[int]


def demand_charge_type_for_node(
    utility_tariff_by_node: dict[str, Any | None],
    node: str,
) -> str | None:
    """Return ``demand_charge_type`` if flat/tou/both; else ``None``."""
    utility_rate_for_node = utility_tariff_by_node.get(node)
    demand_charges = (
        getattr(utility_rate_for_node, "demand_charges", None)
        if utility_rate_for_node is not None
        else None
    )
    demand_charge_type = demand_charges.get("demand_charge_type") if isinstance(demand_charges, dict) else None
    if demand_charge_type in ("flat", "tou", "both"):
        return demand_charge_type
    return None


# --- resolve_utility_inputs: energy prices, tariff objects, demand prerequisites, fixed fees ---


def resolve_utility_inputs(model: pyo.Block, data: DataContainer) -> ResolvedUtilityInputs | None:
    """Merge node-scoped prices/rates, validate demand-charge prerequisites, fixed charges.

    Returns ``None`` if the utility block should not be built (no energy, demand, or fixed fees).
    """
    import_prices_by_node = getattr(model, "import_prices_by_node", None)
    utility_rate_by_node = getattr(model, "utility_rate_by_node", None)

    T = model.T
    nodes = list(model.NODES)
    time_indices = list(T)
    datetimes = data.timeseries.get("datetime")
    if datetimes is None or len(datetimes) != len(time_indices):
        datetimes = [None] * len(time_indices)

    # --- Energy charges: per-node import price ($/kWh) over the horizon ---
    prices_by_node: dict[str, list[float]] = {}
    zero_prices = [0.0] * len(time_indices)
    # --- Tariffs per node (demand schedules/rates + fixed customer metadata) ---
    utility_tariff_by_node: dict[str, Any | None] = {}
    for node in nodes:
        import_price_series = None
        if isinstance(import_prices_by_node, dict):
            import_price_series = import_prices_by_node.get(node)
        if import_price_series is not None:
            prices_by_node[node] = (
                import_price_series
                if len(import_price_series) == len(time_indices)
                else list(import_price_series)
            )
        else:
            prices_by_node[node] = zero_prices

        parsed_tariff = None
        if isinstance(utility_rate_by_node, dict):
            parsed_tariff = utility_rate_by_node.get(node)
        utility_tariff_by_node[node] = parsed_tariff

    # --- Demand charges: if any node has flat/tou/both, require timestep duration and valid datetimes ---
    has_any_demand_charges = any(
        demand_charge_type_for_node(utility_tariff_by_node, node) is not None for node in nodes
    )
    dt_hours_f: float | None = None
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

    # --- Fixed customer charges (usage-independent fees; prorated over horizon using datetimes) ---
    fixed_usd = sum(
        fixed_customer_charges_horizon_usd(
            getattr(utility_tariff_by_node[node], "customer_fixed_charges", None)
            if utility_tariff_by_node[node] is not None
            else None,
            datetimes,
        )
        for node in nodes
    )

    # --- Build the block only if something billable is present ---
    has_node_energy_prices = isinstance(import_prices_by_node, dict) and bool(import_prices_by_node)
    has_energy_or_demand = has_node_energy_prices or has_any_demand_charges
    if not has_energy_or_demand and fixed_usd == 0:
        return None

    return ResolvedUtilityInputs(
        prices_by_node=prices_by_node,
        utility_tariff_by_node=utility_tariff_by_node,
        has_any_demand_charges=has_any_demand_charges,
        dt_hours_f=dt_hours_f,
        datetimes=datetimes,
        fixed_usd=fixed_usd,
        has_node_energy_prices=has_node_energy_prices,
        time_indices=time_indices,
    )
