"""Compressed-gas hydrogen storage inputs: defaults, validation, and resolution.

All stored energy and hydrogen flow variables use **kWh-H2 on an LHV basis** (**kWh-H2_LHV**).
**HHV is not used** internally.

``compression_kwh_electric_per_kwh_h2_lhv`` is auxiliary electricity (e.g. **compressor work**) per
**kWh-H2_LHV** of hydrogen charged into storage in each timestep. This first implementation assumes
**compressed-gas** storage only (no liquefaction); liquefaction could be represented with a larger
coefficient or a separate technology later.

Optional mass-based reporting can use LHV outside the model; a common LHV for H2 is ~33.33 kWh/kg
(at ~120 MJ/kg LHV), but **the optimization uses kWh-H2_LHV only**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.financials import annualization_factor_debt_equity


DEFAULT_COMPRESSED_GAS_H2_STORAGE_PARAMS = {
    "allow_adoption": True,
    "charge_efficiency": 0.98,
    "discharge_efficiency": 0.98,
    "capital_cost_per_kwh_h2_lhv": 50.0,
    "om_per_kwh_h2_lhv_year": 2.0,
    "hydrogen_inventory_retention": 0.9999,
    "minimum_hydrogen_inventory_fraction": 0.0,
    "maximum_hydrogen_inventory_fraction": 1.0,
    "max_hydrogen_charge_per_kwh_capacity": 1.0,
    "max_hydrogen_discharge_per_kwh_capacity": 1.0,
    "compression_kwh_electric_per_kwh_h2_lhv": 0.05,
    "existing_energy_capacity_kwh_h2_lhv_by_node": None,
    "initial_hydrogen_inventory_fraction": None,
}


@dataclass(frozen=True)
class CompressedGasHydrogenStorageBlockInputs:
    """Resolved parameters for the compressed-gas hydrogen storage block."""

    allow_adoption: bool
    charge_efficiency: float
    discharge_efficiency: float
    capital_cost_per_kwh_h2_lhv: float
    om_per_kwh_h2_lhv_year: float
    hydrogen_inventory_retention: float
    minimum_hydrogen_inventory_fraction: float
    maximum_hydrogen_inventory_fraction: float
    max_hydrogen_charge_per_kwh_capacity: float
    max_hydrogen_discharge_per_kwh_capacity: float
    compression_kwh_electric_per_kwh_h2_lhv: float
    existing_energy_capacity_kwh_h2_lhv: dict[str, float]
    amortization_factor: float
    initial_hydrogen_inventory_fraction: float | None


def resolve_compressed_gas_hydrogen_storage_block_inputs(
    h2_storage_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> CompressedGasHydrogenStorageBlockInputs:
    """Merge defaults with user overrides and resolve per-node parameters."""
    params = (h2_storage_params or {}).copy()
    for key, value in DEFAULT_COMPRESSED_GAS_H2_STORAGE_PARAMS.items():
        params.setdefault(key, value)

    allow_adoption = bool(params["allow_adoption"])
    charge_efficiency = float(params["charge_efficiency"])
    discharge_efficiency = float(params["discharge_efficiency"])
    if not (0 < charge_efficiency <= 1) or not (0 < discharge_efficiency <= 1):
        raise ValueError(
            "compressed_gas_hydrogen_storage: charge_efficiency and discharge_efficiency "
            "must each be in (0, 1]."
        )

    capital_cost_per_kwh_h2_lhv = float(params["capital_cost_per_kwh_h2_lhv"])
    om_per_kwh_h2_lhv_year = float(params["om_per_kwh_h2_lhv_year"])
    hydrogen_inventory_retention = float(params["hydrogen_inventory_retention"])
    if not (0 < hydrogen_inventory_retention <= 1):
        raise ValueError(
            "compressed_gas_hydrogen_storage: hydrogen_inventory_retention must be in (0, 1]."
        )

    minimum_hydrogen_inventory_fraction = float(params["minimum_hydrogen_inventory_fraction"])
    maximum_hydrogen_inventory_fraction = float(params["maximum_hydrogen_inventory_fraction"])
    if not (0 <= minimum_hydrogen_inventory_fraction <= 1) or not (
        0 <= maximum_hydrogen_inventory_fraction <= 1
    ):
        raise ValueError(
            "compressed_gas_hydrogen_storage: min/max hydrogen inventory fractions must be in [0, 1]."
        )
    if minimum_hydrogen_inventory_fraction > maximum_hydrogen_inventory_fraction:
        raise ValueError(
            "compressed_gas_hydrogen_storage: minimum_hydrogen_inventory_fraction must be <= maximum."
        )

    max_hydrogen_charge_per_kwh_capacity = float(params["max_hydrogen_charge_per_kwh_capacity"])
    max_hydrogen_discharge_per_kwh_capacity = float(params["max_hydrogen_discharge_per_kwh_capacity"])
    if max_hydrogen_charge_per_kwh_capacity < 0 or max_hydrogen_discharge_per_kwh_capacity < 0:
        raise ValueError(
            "compressed_gas_hydrogen_storage: max charge/discharge per kWh capacity must be >= 0."
        )

    compression_kwh_electric_per_kwh_h2_lhv = float(params["compression_kwh_electric_per_kwh_h2_lhv"])
    if compression_kwh_electric_per_kwh_h2_lhv < 0:
        raise ValueError(
            "compressed_gas_hydrogen_storage: compression_kwh_electric_per_kwh_h2_lhv must be >= 0."
        )

    existing_raw = params.get("existing_energy_capacity_kwh_h2_lhv_by_node") or {}
    existing_energy_capacity_kwh_h2_lhv: dict[str, float] = {}
    for node in nodes:
        e = float(existing_raw.get(node, 0.0))
        if e < 0:
            raise ValueError(
                f"compressed_gas_hydrogen_storage: existing capacity for node {node!r} must be >= 0, got {e}."
            )
        existing_energy_capacity_kwh_h2_lhv[node] = e

    initial_hydrogen_inventory_fraction = params.get("initial_hydrogen_inventory_fraction")
    if initial_hydrogen_inventory_fraction is not None:
        initial_hydrogen_inventory_fraction = float(initial_hydrogen_inventory_fraction)
        if not (
            minimum_hydrogen_inventory_fraction
            <= initial_hydrogen_inventory_fraction
            <= maximum_hydrogen_inventory_fraction
        ):
            raise ValueError(
                "compressed_gas_hydrogen_storage: initial_hydrogen_inventory_fraction must lie between "
                "minimum and maximum hydrogen inventory fractions (inclusive)."
            )

    if min(capital_cost_per_kwh_h2_lhv, om_per_kwh_h2_lhv_year) < 0:
        raise ValueError("compressed_gas_hydrogen_storage: cost inputs must be >= 0.")

    return CompressedGasHydrogenStorageBlockInputs(
        allow_adoption=allow_adoption,
        charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency,
        capital_cost_per_kwh_h2_lhv=capital_cost_per_kwh_h2_lhv,
        om_per_kwh_h2_lhv_year=om_per_kwh_h2_lhv_year,
        hydrogen_inventory_retention=hydrogen_inventory_retention,
        minimum_hydrogen_inventory_fraction=minimum_hydrogen_inventory_fraction,
        maximum_hydrogen_inventory_fraction=maximum_hydrogen_inventory_fraction,
        max_hydrogen_charge_per_kwh_capacity=max_hydrogen_charge_per_kwh_capacity,
        max_hydrogen_discharge_per_kwh_capacity=max_hydrogen_discharge_per_kwh_capacity,
        compression_kwh_electric_per_kwh_h2_lhv=compression_kwh_electric_per_kwh_h2_lhv,
        existing_energy_capacity_kwh_h2_lhv=existing_energy_capacity_kwh_h2_lhv,
        amortization_factor=annualization_factor_debt_equity(**(financials or {})),
        initial_hydrogen_inventory_fraction=initial_hydrogen_inventory_fraction,
    )
