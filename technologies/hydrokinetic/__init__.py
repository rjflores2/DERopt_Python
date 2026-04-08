"""Hydrokinetic (run-of-river / hydrokite) technology package."""

from .block import add_hydrokinetic_block
from .inputs import (
    DEFAULT_HYDROKINETIC_PARAMS,
    FORMULATION_HYDROKINETIC_LP,
    FORMULATION_HYDROKINETIC_UNIT_MILP,
)


def register(
    model,
    data,
    *,
    technology_parameters=None,
    financials=None,
):
    """Registry hook: requires HKT timeseries on ``data`` and non-None ``technology_parameters['hydrokinetic']``."""
    if not data.static.get("hydrokinetic_production_keys"):
        return None
    hk_params = (technology_parameters or {}).get("hydrokinetic")
    if hk_params is None:
        return None
    return add_hydrokinetic_block(
        model,
        data,
        hydrokinetic_params=hk_params if isinstance(hk_params, dict) else {},
        financials=financials or {},
    )


__all__ = [
    "DEFAULT_HYDROKINETIC_PARAMS",
    "FORMULATION_HYDROKINETIC_LP",
    "FORMULATION_HYDROKINETIC_UNIT_MILP",
    "add_hydrokinetic_block",
    "register",
]
