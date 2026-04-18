"""PEM electrolyzer technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_pem_electrolyzer_block


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the PEM electrolyzer block.

    - ``technology_parameters["pem_electrolyzer"]`` -> dict passed as ``pem_electrolyzer_params``.
    """
    pem_electrolyzer_params = (technology_parameters or {}).get("pem_electrolyzer") or {}
    return add_pem_electrolyzer_block(
        model,
        data,
        pem_electrolyzer_params=pem_electrolyzer_params,
        financials=financials or {},
    )


__all__ = [
    "add_pem_electrolyzer_block",
    "register",
]
