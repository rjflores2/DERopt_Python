"""Battery defaults, validation, and parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


DEFAULT_BATTERY_PARAMS = {
    "allow_adoption": True,
    "charge_efficiency": 0.95,
    "discharge_efficiency": 0.95,
    "capital_cost_per_kwh": 400.0,
    "om_per_kwh_year": 10.0,
    "max_charge_power_per_kwh": 0.5,
    "max_discharge_power_per_kwh": 0.5,
    "existing_energy_capacity_by_node": None,
    "initial_soc_fraction": None,
}


@dataclass
class ResolvedBatteryInputs:
    """All parameter-derived inputs for the battery block (no time series)."""

    charge_efficiency: float
    discharge_efficiency: float
    capital_cost_per_kwh: float
    om_per_kwh_year: float
    max_charge_power_per_kwh: float
    max_discharge_power_per_kwh: float
    existing_energy_capacity: dict[str, float]
    amortization_factor: float
    initial_soc_fraction: float | None


def resolve_battery_block_inputs(
    battery_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> ResolvedBatteryInputs:
    """Merge defaults with user overrides and resolve per-node battery parameters."""
    params = (battery_params or {}).copy()
    for key, value in DEFAULT_BATTERY_PARAMS.items():
        params.setdefault(key, value)

    charge_efficiency = float(params["charge_efficiency"])
    discharge_efficiency = float(params["discharge_efficiency"])
    if not (0 < charge_efficiency <= 1) or not (0 < discharge_efficiency <= 1):
        raise ValueError(
            "battery_energy_storage: charge_efficiency and discharge_efficiency must each be in (0, 1]."
        )

    capital_cost_per_kwh = float(params["capital_cost_per_kwh"])
    om_per_kwh_year = float(params["om_per_kwh_year"])

    max_charge_power_per_kwh = float(params["max_charge_power_per_kwh"])
    max_discharge_power_per_kwh = float(params["max_discharge_power_per_kwh"])
    if max_charge_power_per_kwh <= 0 or max_discharge_power_per_kwh <= 0:
        raise ValueError(
            "battery_energy_storage: max_*_power_per_kwh must be > 0 (C-rate)."
        )

    existing_raw = params.get("existing_energy_capacity_by_node") or {}
    existing_energy_capacity: dict[str, float] = {}
    for node in nodes:
        value = float(existing_raw.get(node, 0.0))
        if value < 0:
            raise ValueError(
                f"battery_energy_storage: existing_energy_capacity for node {node!r} must be >= 0, got {value}."
            )
        existing_energy_capacity[node] = value

    initial_soc_fraction = params.get("initial_soc_fraction")
    if initial_soc_fraction is not None:
        initial_soc_fraction = float(initial_soc_fraction)
        if not (0 <= initial_soc_fraction <= 1):
            raise ValueError(
                "battery_energy_storage: initial_soc_fraction must be between 0 and 1."
            )

    return ResolvedBatteryInputs(
        charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency,
        capital_cost_per_kwh=capital_cost_per_kwh,
        om_per_kwh_year=om_per_kwh_year,
        max_charge_power_per_kwh=max_charge_power_per_kwh,
        max_discharge_power_per_kwh=max_discharge_power_per_kwh,
        existing_energy_capacity=existing_energy_capacity,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
        initial_soc_fraction=initial_soc_fraction,
    )
