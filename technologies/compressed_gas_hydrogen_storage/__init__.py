"""Compressed-gas hydrogen storage technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_compressed_gas_hydrogen_storage_block


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the compressed-gas hydrogen storage block.

    - ``technology_parameters["compressed_gas_hydrogen_storage"]`` -> dict passed as params.
    """
    params = (technology_parameters or {}).get("compressed_gas_hydrogen_storage") or {}
    return add_compressed_gas_hydrogen_storage_block(
        model,
        data,
        compressed_gas_hydrogen_storage_params=params,
        financials=financials or {},
    )


__all__ = [
    "add_compressed_gas_hydrogen_storage_block",
    "register",
]
