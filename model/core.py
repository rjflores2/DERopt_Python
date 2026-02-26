"""Core model assembly: sets, optional technology blocks, and registration.

Builds a Pyomo ConcreteModel with time set T. When data is provided and contains
solar resource data, the Solar PV block is attached. Generation from the solar
block is exposed for use in the electricity balance (in core or a separate balance module).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyomo.environ as pyo

if TYPE_CHECKING:
    from data_loading.schemas import DataContainer


def build_model(
    data: DataContainer | None = None,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.ConcreteModel | None:
    """Build a Pyomo model with time set T and attach technology blocks when data supports them.

    Returns:
        ConcreteModel with model.T (time set) and optionally model.solar_pv (Block).
        Returns None if data is None (backward compatibility).
    """
    if data is None:
        return None

    model = pyo.ConcreteModel()
    model.T = pyo.Set(initialize=range(len(data.indices["time"])), ordered=True)

    # Attach technology blocks when data supports them
    from technologies.solar_pv import register as register_solar_pv

    tech_params = technology_parameters or {}
    register_solar_pv(
        model,
        data,
        solar_pv_params=(tech_params.get("solar_pv") or {}),
        financials=(financials or {}),
    )

    return model
