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
    # No data: return None so callers can handle "no model" without error.
    if data is None:
        return None

    # -------------------------------------------------------------------------
    # Base model and index sets
    # -------------------------------------------------------------------------
    model = pyo.ConcreteModel()
    # Time set: one index per period (e.g. 0..8759 for hourly over a year).
    model.T = pyo.Set(initialize=range(len(data.indices["time"])), ordered=True)

    # Node set: one entry per load series (from load data); used for per-node balance and tech blocks.
    load_keys = data.static.get("electricity_load_keys") or []
    if not load_keys:
        raise ValueError("model requires data.static['electricity_load_keys'] (load data first)")
    model.NODES = pyo.Set(initialize=list(load_keys), ordered=True)

    # -------------------------------------------------------------------------
    # Technology blocks (opt-in via technology_parameters)
    # -------------------------------------------------------------------------
    from technologies import REGISTRY

    tech_params = technology_parameters or {}
    fin = financials or {}
    for key, register_fn in REGISTRY:
        # Skip techs not listed in config or explicitly set to None.
        if tech_params.get(key) is None:
            continue
        register_fn(
            model,
            data,
            technology_parameters=tech_params,
            financials=fin,
        )

    # -------------------------------------------------------------------------
    # Electricity balance: sources == sinks (per node, per time)
    # Sources = generation + imports + storage discharge.
    # Sinks = load + storage charging. Balance enforced for each (node, t).
    # -------------------------------------------------------------------------
    _nodes = list(model.NODES)
    _T = list(model.T)
    # Load from data: kWh at each (node, t). Keys match electricity_load_keys; series length = len(T).
    load_init = {
        (n, t): float(data.timeseries[n][t])
        for n in _nodes
        for t in _T
    }
    model.electricity_load = pyo.Param(
        model.NODES,
        model.T,
        initialize=load_init,
        within=pyo.NonNegativeReals,
        mutable=True,
    )

    # Sources: sum of electricity_source_term over all top-level blocks that define it (e.g. solar_pv).
    def _sources_rule(m, n, t):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "electricity_source_term"):
                total += blk.electricity_source_term[n, t]
        return total

    model.electricity_sources = pyo.Expression(model.NODES, model.T, rule=_sources_rule)

    # Sinks: for now load only; extend with storage charging etc. when those modules exist.
    def _sinks_rule(m, n, t):
        return m.electricity_load[n, t]

    model.electricity_sinks = pyo.Expression(model.NODES, model.T, rule=_sinks_rule)

    # Balance constraint: sources must equal sinks at each (node, t).
    def _balance_rule(m, n, t):
        return m.electricity_sources[n, t] == m.electricity_sinks[n, t]

    model.electricity_balance = pyo.Constraint(model.NODES, model.T, rule=_balance_rule)

    # -------------------------------------------------------------------------
    # Objective: minimize total cost (sum of objective_contribution from all
    # technology blocks and utility blocks that define it).
    # -------------------------------------------------------------------------
    def _objective_rule(m):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "objective_contribution"):
                total += blk.objective_contribution
        return total

    model.obj = pyo.Objective(rule=_objective_rule, sense=pyo.minimize)

    # -------------------------------------------------------------------------
    # Reporting: total annual cost from existing assets (sunk / fixed).
    # Blocks may define cost_existing_annual (e.g. O&M on existing, remaining
    # debt on existing); summed here for post-processing and cost breakdown.
    # -------------------------------------------------------------------------
    def _cost_existing_rule(m):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "cost_existing_annual"):
                total += blk.cost_existing_annual
        return total

    model.total_cost_existing_annual = pyo.Expression(rule=_cost_existing_rule)

    return model
