"""Flow battery energy storage technology package (decoupled energy and power capacity)."""

from .block import add_flow_battery_energy_storage_block
from .diagnostics import collect_equipment_cost_diagnostics


def register(
    model,
    data,
    *,
    technology_parameters=None,
    financials=None,
):
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
