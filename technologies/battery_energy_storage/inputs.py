"""Battery defaults, validation, and parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


DEFAULT_BATTERY_PARAMS = {
    "allow_adoption": True,
    "charge_efficiency": 0.95, # efficiency of charging energy from the grid to the battery
    "discharge_efficiency": 0.95, # efficiency of discharging energy from battery to the grid
    "capital_cost_per_kwh": 400.0, # $/kWh adopted capacuty
    "om_per_kwh_year": 10.0, # $/kWh/year
    "max_charge_power_per_kwh": 0.5, # C-rate, or kW charging power / kWh capacity
    "max_discharge_power_per_kwh": 0.5, # C-rate, or kW discharging power / kWh capacity
    "state_of_charge_retention": 0.99995, # fraction of SOC retained each timestep (1 - loss)
    # Usable window as fractions of total energy capacity (existing + adopted), 0..1.
    "minimum_state_of_charge": 0.0,
    "maximum_state_of_charge": 1.0,
    "existing_energy_capacity_by_node": None, # kWh existing capacity by node
    "initial_soc_fraction": None, # initial state of charge fraction - why do we need this?
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
    state_of_charge_retention: float
    minimum_state_of_charge: float
    maximum_state_of_charge: float
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
    state_of_charge_retention = float(params["state_of_charge_retention"])
    if not (0 < state_of_charge_retention <= 1):
        raise ValueError(
            "battery_energy_storage: state_of_charge_retention must be in (0, 1]."
        )

    minimum_state_of_charge = float(params["minimum_state_of_charge"])
    maximum_state_of_charge = float(params["maximum_state_of_charge"])
    if not (0 <= minimum_state_of_charge <= 1) or not (0 <= maximum_state_of_charge <= 1):
        raise ValueError(
            "battery_energy_storage: minimum_state_of_charge and maximum_state_of_charge must each be in [0, 1]."
        )
    if minimum_state_of_charge > maximum_state_of_charge:
        raise ValueError(
            "battery_energy_storage: minimum_state_of_charge must be <= maximum_state_of_charge."
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
        if not (minimum_state_of_charge <= initial_soc_fraction <= maximum_state_of_charge):
            raise ValueError(
                "battery_energy_storage: initial_soc_fraction must lie between "
                "minimum_state_of_charge and maximum_state_of_charge (inclusive)."
            )

    return ResolvedBatteryInputs(
        charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency,
        capital_cost_per_kwh=capital_cost_per_kwh,
        om_per_kwh_year=om_per_kwh_year,
        max_charge_power_per_kwh=max_charge_power_per_kwh,
        max_discharge_power_per_kwh=max_discharge_power_per_kwh,
        state_of_charge_retention=state_of_charge_retention,
        minimum_state_of_charge=minimum_state_of_charge,
        maximum_state_of_charge=maximum_state_of_charge,
        existing_energy_capacity=existing_energy_capacity,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
        initial_soc_fraction=initial_soc_fraction,
    )
