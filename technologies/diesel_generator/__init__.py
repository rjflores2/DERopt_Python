"""Diesel generator technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_diesel_generator_block


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the diesel generator block via ``add_diesel_generator_block``.

    - ``technology_parameters["diesel_generator"]`` -> dict passed as ``diesel_generator_params``.
    """
    diesel_generator_params = (technology_parameters or {}).get("diesel_generator") or {}
    return add_diesel_generator_block(
        model,
        data,
        diesel_generator_params=diesel_generator_params,
        financials=financials or {},
    )


__all__ = [
    "add_diesel_generator_block",
    "register",
]
