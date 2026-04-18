"""Alkaline electrolyzer technology package."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_alkaline_electrolyzer_block


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Registry hook: build the alkaline electrolyzer block.

    - ``technology_parameters["alkaline_electrolyzer"]`` -> dict passed as ``alkaline_electrolyzer_params``.
    """
    alkaline_electrolyzer_params = (technology_parameters or {}).get("alkaline_electrolyzer") or {}
    return add_alkaline_electrolyzer_block(
        model,
        data,
        alkaline_electrolyzer_params=alkaline_electrolyzer_params,
        financials=financials or {},
    )


__all__ = [
    "add_alkaline_electrolyzer_block",
    "register",
]
