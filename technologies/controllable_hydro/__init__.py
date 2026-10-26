"""Controllable hydro technology package: conventional dam hydro and pumped storage hydro
in one set-indexed formulation (no adoption; all hydro capacity is existing)."""

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .block import add_controllable_hydro_block
from .inputs import (
    BOUNDARY_CYCLIC,
    BOUNDARY_FIXED_ENDPOINTS,
    BOUNDARY_FREE_END,
    DEFAULT_CONTROLLABLE_HYDRO_PARAMS,
)


def register(
    model: pyo.Block,
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """Registry hook: requires unit keys on ``data`` and non-None
    ``technology_parameters['controllable_hydro']``."""
    if not data.static.get("controllable_hydro_unit_keys"):
        return None
    hydro_params = (technology_parameters or {}).get("controllable_hydro")
    if hydro_params is None:
        return None
    return add_controllable_hydro_block(
        model,
        data,
        controllable_hydro_params=hydro_params if isinstance(hydro_params, dict) else {},
        financials=financials or {},
    )


__all__ = [
    "BOUNDARY_CYCLIC",
    "BOUNDARY_FIXED_ENDPOINTS",
    "BOUNDARY_FREE_END",
    "DEFAULT_CONTROLLABLE_HYDRO_PARAMS",
    "add_controllable_hydro_block",
    "register",
]
