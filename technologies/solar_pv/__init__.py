"""Solar PV technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_solar_pv_block
from .diagnostics import collect_equipment_cost_diagnostics


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """
    Registry hook: if ``data.static["solar_production_keys"]`` is non-empty, call
    ``add_solar_pv_block``; else return ``None``.

    - ``technology_parameters["solar_pv"]`` -> dict passed as ``solar_pv_params``.
    - ``financials`` -> passed through for amortization on adopted solar.
    """
    if not data.static.get("solar_production_keys"):
        return None
    solar_pv_params = (technology_parameters or {}).get("solar_pv") or {}
    return add_solar_pv_block(
        model,
        data,
        solar_pv_params=solar_pv_params,
        financials=financials or {},
    )


__all__ = [
    "add_solar_pv_block",
    "collect_equipment_cost_diagnostics",
    "register",
]
