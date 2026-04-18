"""Battery energy storage technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_battery_energy_storage_block
from .diagnostics import collect_equipment_cost_diagnostics


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the battery block via ``add_battery_energy_storage_block``.

    - ``technology_parameters["battery_energy_storage"]`` -> dict passed as ``battery_params``.
    """
    battery_params = (technology_parameters or {}).get("battery_energy_storage") or {}
    return add_battery_energy_storage_block(
        model,
        data,
        battery_params=battery_params,
        financials=financials or {},
    )


__all__ = [
    "add_battery_energy_storage_block",
    "collect_equipment_cost_diagnostics",
    "register",
]
