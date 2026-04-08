"""Flow battery defaults, validation, and parameter resolution.

Unlike ``battery_energy_storage``, energy capacity (kWh) and power capacity (kW) are independent
design quantities—charge/discharge limits use a named power capacity, not C-rates on energy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


DEFAULT_FLOW_BATTERY_PARAMS = {
    "allow_adoption": True,
    "charge_efficiency": 0.95,
    "discharge_efficiency": 0.95,
    "energy_capital_cost_per_kwh": 400.0,
    "power_capital_cost_per_kw": 200.0,
    "energy_om_per_kwh_year": 10.0,
    "power_om_per_kw_year": 5.0,
    "state_of_charge_retention": 0.99995,
    "minimum_state_of_charge": 0.0,
    "maximum_state_of_charge": 1.0,
    "existing_energy_capacity_by_node": None,
    "existing_power_capacity_by_node": None,
    "initial_soc_fraction": None,
}


@dataclass
class ResolvedFlowBatteryInputs:
    """Parameter-derived inputs for the flow battery block (no time series)."""

    charge_efficiency: float
    discharge_efficiency: float
    energy_capital_cost_per_kwh: float
    power_capital_cost_per_kw: float
    energy_om_per_kwh_year: float
    power_om_per_kw_year: float
    state_of_charge_retention: float
    minimum_state_of_charge: float
    maximum_state_of_charge: float
    existing_energy_capacity: dict[str, float]
    existing_power_capacity: dict[str, float]
    amortization_factor: float
    initial_soc_fraction: float | None


def resolve_flow_battery_block_inputs(
    flow_battery_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> ResolvedFlowBatteryInputs:
    """Merge defaults with user overrides and resolve per-node parameters."""
    params = (flow_battery_params or {}).copy()
    for key, value in DEFAULT_FLOW_BATTERY_PARAMS.items():
        params.setdefault(key, value)

    charge_efficiency = float(params["charge_efficiency"])
    discharge_efficiency = float(params["discharge_efficiency"])
    if not (0 < charge_efficiency <= 1) or not (0 < discharge_efficiency <= 1):
        raise ValueError(
            "flow_battery_energy_storage: charge_efficiency and discharge_efficiency must each be in (0, 1]."
        )

    energy_capital_cost_per_kwh = float(params["energy_capital_cost_per_kwh"])
    power_capital_cost_per_kw = float(params["power_capital_cost_per_kw"])
    energy_om_per_kwh_year = float(params["energy_om_per_kwh_year"])
    power_om_per_kw_year = float(params["power_om_per_kw_year"])

    state_of_charge_retention = float(params["state_of_charge_retention"])
    if not (0 < state_of_charge_retention <= 1):
        raise ValueError(
            "flow_battery_energy_storage: state_of_charge_retention must be in (0, 1]."
        )

    minimum_state_of_charge = float(params["minimum_state_of_charge"])
    maximum_state_of_charge = float(params["maximum_state_of_charge"])
    if not (0 <= minimum_state_of_charge <= 1) or not (0 <= maximum_state_of_charge <= 1):
        raise ValueError(
            "flow_battery_energy_storage: minimum_state_of_charge and maximum_state_of_charge "
            "must each be in [0, 1]."
        )
    if minimum_state_of_charge > maximum_state_of_charge:
        raise ValueError(
            "flow_battery_energy_storage: minimum_state_of_charge must be <= maximum_state_of_charge."
        )

    existing_energy_raw = params.get("existing_energy_capacity_by_node") or {}
    existing_power_raw = params.get("existing_power_capacity_by_node") or {}
    existing_energy_capacity: dict[str, float] = {}
    existing_power_capacity: dict[str, float] = {}
    for node in nodes:
        e = float(existing_energy_raw.get(node, 0.0))
        p = float(existing_power_raw.get(node, 0.0))
        if e < 0:
            raise ValueError(
                f"flow_battery_energy_storage: existing_energy_capacity for node {node!r} must be >= 0, got {e}."
            )
        if p < 0:
            raise ValueError(
                f"flow_battery_energy_storage: existing_power_capacity for node {node!r} must be >= 0, got {p}."
            )
        existing_energy_capacity[node] = e
        existing_power_capacity[node] = p

    initial_soc_fraction = params.get("initial_soc_fraction")
    if initial_soc_fraction is not None:
        initial_soc_fraction = float(initial_soc_fraction)
        if not (minimum_state_of_charge <= initial_soc_fraction <= maximum_state_of_charge):
            raise ValueError(
                "flow_battery_energy_storage: initial_soc_fraction must lie between "
                "minimum_state_of_charge and maximum_state_of_charge (inclusive)."
            )

    return ResolvedFlowBatteryInputs(
        charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency,
        energy_capital_cost_per_kwh=energy_capital_cost_per_kwh,
        power_capital_cost_per_kw=power_capital_cost_per_kw,
        energy_om_per_kwh_year=energy_om_per_kwh_year,
        power_om_per_kw_year=power_om_per_kw_year,
        state_of_charge_retention=state_of_charge_retention,
        minimum_state_of_charge=minimum_state_of_charge,
        maximum_state_of_charge=maximum_state_of_charge,
        existing_energy_capacity=existing_energy_capacity,
        existing_power_capacity=existing_power_capacity,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
        initial_soc_fraction=initial_soc_fraction,
    )
