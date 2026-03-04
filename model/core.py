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

    Only technologies listed in technology_parameters (with a non-None value) are included.
    Omit a key to exclude that technology; use {} for defaults or a dict for overrides.
    Example: technology_parameters = {"solar_pv": {}, "diesel_generation": {...}}
    gives solar (defaults) and diesel (with params); all other techs are off.

    Returns:
        ConcreteModel with model.T (time set), model.NODES (one per load), and optionally
        model.solar_pv (Block). Returns None if data is None (backward compatibility).
    """
    if data is None:
        return None

    model = pyo.ConcreteModel()
    model.T = pyo.Set(initialize=range(len(data.indices["time"])), ordered=True)

    load_keys = data.static.get("electricity_load_keys") or []
    if not load_keys:
        raise ValueError("model requires data.static['electricity_load_keys'] (load data first)")
    model.NODES = pyo.Set(initialize=list(load_keys), ordered=True)

    # Attach technology blocks: only techs present in technology_parameters (value not None) are included.
    from technologies import REGISTRY

    tech_params = technology_parameters or {}
    fin = financials or {}
    for key, register_fn in REGISTRY:
        if tech_params.get(key) is None:
            continue
        register_fn(
            model,
            data,
            technology_parameters=tech_params,
            financials=fin,
        )

    return model
