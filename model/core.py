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

    Technologies: only those in technology_parameters with a non-None value are included; use {} for defaults.
    Utility data (node-scoped prices/rates) is read from data so the model has a single data contract.
    Returns None if data is None (backward compatibility).
    """
    # No data: return None so callers can handle "no model" without error.
    if data is None:
        return None

    # Fail fast if required data fields are missing (don't propagate bad data downstream).
    data.validate_minimum_fields()

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

    n_time = len(data.indices["time"])
    import_prices_by_node = getattr(data, "import_prices_by_node", None)
    utility_rate_by_node = getattr(data, "utility_rate_by_node", None)
    # Normalize any single-tariff data into node-scoped maps so downstream utility
    # code only needs one data shape.
    import_prices = getattr(data, "import_prices", None)
    utility_rate = getattr(data, "utility_rate", None)
    if import_prices is not None and len(import_prices) != n_time:
        raise ValueError(f"data.import_prices length {len(import_prices)} != time steps {n_time}")
    if import_prices_by_node is None and import_prices is not None:
        import_prices_by_node = {n: import_prices for n in load_keys}
    if utility_rate_by_node is None and utility_rate is not None:
        utility_rate_by_node = {n: utility_rate for n in load_keys}
    if import_prices_by_node is not None:
        for n, series in import_prices_by_node.items():
            if n not in load_keys:
                raise ValueError(f"data.import_prices_by_node contains unknown node {n!r}")
            if len(series) != n_time:
                raise ValueError(
                    f"data.import_prices_by_node[{n!r}] length {len(series)} != time steps {n_time}"
                )
    if utility_rate_by_node is not None:
        for n in utility_rate_by_node:
            if n not in load_keys:
                raise ValueError(f"data.utility_rate_by_node contains unknown node {n!r}")
    # Node-scoped utility inputs (prices and parsed rates).
    model.import_prices_by_node = import_prices_by_node
    model.utility_rate_by_node = utility_rate_by_node

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

    # Grid/utility block: grid_import variable, energy cost (import_prices), demand charges (from utility_rate.demand_charges).
    # Attach when we have import_prices or demand-charge data so balance can include grid and objective includes utility cost.
    from utilities.electricity_import_export import register as register_utility_block
    register_utility_block(model, data)

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

    # Sinks: load plus any storage charging or other sink terms defined on blocks.
    def _sinks_rule(m, n, t):
        total = m.electricity_load[n, t]
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "electricity_sink_term"):
                total += blk.electricity_sink_term[n, t]
        return total

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
    # Reporting cost buckets (not used by optimizer unless included in objective_contribution).
    # Keep constant / non-optimizing costs visible for post-processing.
    # -------------------------------------------------------------------------
    def _cost_non_optimizing_rule(m):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "cost_non_optimizing_annual"):
                total += blk.cost_non_optimizing_annual
        return total

    model.total_cost_non_optimizing_annual = pyo.Expression(rule=_cost_non_optimizing_rule)

    # Explicit reporting totals:
    # - optimizing annual cost follows objective_contribution sum
    # - total reported annual cost = optimizing + non-optimizing fixed/background costs
    model.total_optimizing_cost_annual = pyo.Expression(expr=model.obj.expr)
    model.total_reported_annual_cost = pyo.Expression(
        expr=model.total_optimizing_cost_annual + model.total_cost_non_optimizing_annual
    )

    return model
