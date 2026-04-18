"""Flow battery energy storage technology package (decoupled energy and power capacity)."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_flow_battery_energy_storage_block
from .diagnostics import collect_equipment_cost_diagnostics


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the flow battery block via ``add_flow_battery_energy_storage_block``.

    - ``technology_parameters["flow_battery_energy_storage"]`` -> dict passed as ``flow_battery_params``.
    """
    flow_params = (technology_parameters or {}).get("flow_battery_energy_storage") or {}
    return add_flow_battery_energy_storage_block(
        model,
        data,
        flow_battery_params=flow_params,
        financials=financials or {},
    )


__all__ = [
    "add_flow_battery_energy_storage_block",
    "collect_equipment_cost_diagnostics",
    "register",
]
