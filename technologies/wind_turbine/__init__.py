"""Wind turbine technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_wind_turbine_block
from .diagnostics import collect_equipment_cost_diagnostics


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """
    Registry hook: if ``data.static["wind_production_keys"]`` is non-empty, call
    ``add_wind_turbine_block``; else return ``None``.
    """
    if not data.static.get("wind_production_keys"):
        return None
    wind_turbine_params = (technology_parameters or {}).get("wind_turbine") or {}
    return add_wind_turbine_block(
        model,
        data,
        wind_turbine_params=wind_turbine_params,
        financials=financials or {},
    )


__all__ = [
    "add_wind_turbine_block",
    "collect_equipment_cost_diagnostics",
    "register",
]
