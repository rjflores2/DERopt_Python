"""PEM fuel cell technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_pem_fuel_cell_block


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the PEM fuel cell block.

    - ``technology_parameters["pem_fuel_cell"]`` -> dict passed as ``pem_fuel_cell_params``.
    """
    pem_fuel_cell_params = (technology_parameters or {}).get("pem_fuel_cell") or {}
    return add_pem_fuel_cell_block(
        model,
        data,
        pem_fuel_cell_params=pem_fuel_cell_params,
        financials=financials or {},
    )


__all__ = [
    "add_pem_fuel_cell_block",
    "register",
]
