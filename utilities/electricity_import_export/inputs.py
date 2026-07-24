"""Resolve and validate utility-block inputs from ``model`` and ``data`` (no Pyomo)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from data_loading.loaders.utility_rates.customer_charge_horizon import (
    fixed_customer_charges_horizon_usd,
)
from data_loading.schemas import DataContainer
from shared.scenario_helpers import (
    has_any_scenario_utility_override,
    import_price_for_scenario,
    utility_rate_for_scenario,
)


@dataclass(frozen=True)
class ResolvedUtilityInputs:
    """Everything needed to build ``model.utility`` except Pyomo objects.

    Prices and tariffs are resolved **per scenario** (a scenario without its own
    ``utility_tariffs`` override falls back to the base/case-level values -- see
    ``shared.scenario_helpers``), since each scenario is a Stage-2 recourse decision and can
    realistically face a different energy price or demand-charge structure (e.g. a "dry"
    hydro-year scenario correlated with a different regional grid price).

    ``fixed_usd`` (customer fixed charges) is deliberately kept scenario-independent,
    resolved from the base-case tariff only: it is a reporting-only, non-optimizing
    quantity (``cost_non_optimizing_annual`` must stay a scalar per ``model/contracts.py``),
    and a flat monthly account fee is not the kind of quantity that plausibly varies with
    hydrology/weather scenarios the way energy prices and demand charges can.
    """

    scenario_keys: list[str]
    prices_by_node_by_scenario: dict[str, dict[str, list[float]]]
    utility_tariff_by_node_by_scenario: dict[str, dict[str, Any | None]]
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
    """Merge node-scoped prices/rates (per scenario), validate demand-charge prerequisites,
    resolve fixed charges.

    Returns ``None`` if the utility block should not be built (no energy, demand, or fixed fees).
    """
    import_prices_by_node = getattr(model, "import_prices_by_node", None)
    utility_rate_by_node = getattr(model, "utility_rate_by_node", None)
    scenario_keys = list(model.SCENARIOS)

    # Islanded / no utility data anywhere: both the base AND every scenario are unset. A
    # scenario-only utility_tariffs override (base islanded/free_grid, but individual
    # scenarios configure real tariffs) must still build the block -- checking only the
    # base here would silently drop every scenario's configured grid cost.
    if (
        import_prices_by_node is None
        and utility_rate_by_node is None
        and not has_any_scenario_utility_override(data, scenario_keys)
    ):
        return None

    T = model.T
    nodes = list(model.NODES)
    time_indices = list(T)
    datetimes = data.timeseries.get("datetime")
    if datetimes is None or len(datetimes) != len(time_indices):
        datetimes = [None] * len(time_indices)

    zero_prices = [0.0] * len(time_indices)

    # --- Energy charges + tariffs, resolved per scenario via the shared fallback helpers
    # (scenario override, else base; None if neither defines a value for this node). ---
    has_node_energy_prices = False
    prices_by_node_by_scenario: dict[str, dict[str, list[float]]] = {}
    utility_tariff_by_node_by_scenario: dict[str, dict[str, Any | None]] = {}
    for s in scenario_keys:
        prices_by_node: dict[str, list[float]] = {}
        utility_tariff_by_node: dict[str, Any | None] = {}
        for node in nodes:
            import_price_series = import_price_for_scenario(data, s, node)
            if import_price_series is not None:
                if len(import_price_series) != len(time_indices):
                    raise ValueError(
                        f"scenario {s!r}, node {node!r}: import price series has length "
                        f"{len(import_price_series)} but the run has {len(time_indices)} "
                        "periods. This should have been caught by the loader; a caller "
                        "must be constructing DataContainer.import_prices_by_node or "
                        "scenario_import_prices_by_node directly with a mismatched length."
                    )
                has_node_energy_prices = True
                prices_by_node[node] = import_price_series
            else:
                prices_by_node[node] = zero_prices

            utility_tariff_by_node[node] = utility_rate_for_scenario(data, s, node)

        prices_by_node_by_scenario[s] = prices_by_node
        utility_tariff_by_node_by_scenario[s] = utility_tariff_by_node

    # --- Demand charges: if any (scenario, node) has flat/tou/both, require timestep
    # duration and valid datetimes -- a scenario-level tariff override can introduce demand
    # charges even when the base case has none, so every scenario must be checked. ---
    has_any_demand_charges = any(
        demand_charge_type_for_node(utility_tariff_by_node_by_scenario[s], node) is not None
        for s in scenario_keys
        for node in nodes
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

    # --- Fixed customer charges (usage-independent fees; prorated over horizon using
    # datetimes). Scenario-independent by design -- see ResolvedUtilityInputs docstring.
    # Resolved from the true base data.utility_rate_by_node directly (not via any
    # scenario's resolution), so it doesn't depend on which scenario happens to be first. ---
    def _base_tariff_for_node(node: str) -> Any | None:
        return utility_rate_by_node.get(node) if isinstance(utility_rate_by_node, dict) else None

    fixed_usd = sum(
        fixed_customer_charges_horizon_usd(
            getattr(_base_tariff_for_node(node), "customer_fixed_charges", None),
            datetimes,
        )
        for node in nodes
    )

    # --- Build the block only if something billable is present. has_node_energy_prices was
    # tracked during the per-scenario/per-node resolution loop above, so it already reflects
    # scenario-only price overrides, not just the base case. ---
    has_energy_or_demand = has_node_energy_prices or has_any_demand_charges
    if not has_energy_or_demand and fixed_usd == 0:
        return None

    return ResolvedUtilityInputs(
        scenario_keys=scenario_keys,
        prices_by_node_by_scenario=prices_by_node_by_scenario,
        utility_tariff_by_node_by_scenario=utility_tariff_by_node_by_scenario,
        has_any_demand_charges=has_any_demand_charges,
        dt_hours_f=dt_hours_f,
        datetimes=datetimes,
        fixed_usd=fixed_usd,
        has_node_energy_prices=has_node_energy_prices,
        time_indices=time_indices,
    )
