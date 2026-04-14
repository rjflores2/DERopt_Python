"""Core model assembly: sets, optional technology blocks, and registration.

Builds a Pyomo ConcreteModel with time set T. Technology blocks are attached from the
registry when listed in ``technology_parameters``. The model enforces an **electricity**
balance and a **hydrogen** balance (kWh-H2 LHV per timestep) by summing
``electricity_*_term`` / ``hydrogen_*_term`` contributions on each top-level block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyomo.environ as pyo

if TYPE_CHECKING:
    from data_loading.schemas import DataContainer

from .contracts import validate_technology_block_interface


def build_model(
    data: DataContainer,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.ConcreteModel:
    """Build a Pyomo model with time set T and attach technology blocks when data supports them.

    Technologies: for each registry name, ``technology_parameters.get(name)`` must be non-``None`` to build
    (missing key omits). Use ``{}`` for module defaults; explicit ``None`` omits. If a technology is requested
    but ``register`` returns ``None`` without attaching ``model.<name>``, this raises (see README).
    Utility data (node-scoped prices/rates) is read from data so the model has a single data contract.
    """
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
    # Validate load-series length up front so errors are clear before Pyomo indexing.
    for node in load_keys:
        series = data.timeseries.get(node)
        if not isinstance(series, list):
            raise ValueError(f"data.timeseries[{node!r}] must be a list with one value per time step")
        if len(series) != n_time:
            raise ValueError(
                f"data.timeseries[{node!r}] length {len(series)} != time steps {n_time}"
            )

    # Utility inputs are canonicalized upstream as node-scoped maps.
    import_prices_by_node = getattr(data, "import_prices_by_node", None)
    utility_rate_by_node = getattr(data, "utility_rate_by_node", None)
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
    #  Assigning the import prices and utility rate to the model
    model.import_prices_by_node = import_prices_by_node
    model.utility_rate_by_node = utility_rate_by_node

    # -------------------------------------------------------------------------
    # Technology blocks (opt-in via technology_parameters)
    # -------------------------------------------------------------------------
    from technologies import REGISTRY

    tech_params = technology_parameters or {}
    fin = financials or {}
    for technology_name, register_fn in REGISTRY:
        # Skip techs not listed in config or explicitly set to None.
        if tech_params.get(technology_name) is None:
            continue
        returned = register_fn(
            model,
            data,
            technology_parameters=tech_params,
            financials=fin,
        )
        blk = getattr(model, technology_name, None)
        if returned is not None:
            if blk is None:
                raise ValueError(
                    f"technology {technology_name!r}: register() returned a Block but "
                    f"model.{technology_name!r} is missing. Registry hooks must assign "
                    f"model.{technology_name} = <Block> to the same object they return."
                )
            if not isinstance(blk, pyo.Block):
                raise ValueError(
                    f"technology {technology_name!r}: model.{technology_name!r} must be a "
                    f"pyo.Block when register() returns a block; got {type(blk).__name__!r}."
                )
            if returned is not blk:
                raise ValueError(
                    f"technology {technology_name!r}: register() must return the exact Block "
                    f"attached as model.{technology_name!r} (identity mismatch)."
                )
            validate_technology_block_interface(
                technology_key=technology_name,
                block=blk,
                model=model,
            )
        elif blk is not None:
            raise ValueError(
                f"technology {technology_name!r}: register() returned None but "
                f"model.{technology_name!r} exists. Remove the stray attribute or return "
                f"that Block from register()."
            )
        else:
            raise ValueError(
                f"technology {technology_name!r} was requested in technology_parameters but "
                f"register() returned None and model.{technology_name!r} was not attached. "
                f"Registry technologies must attach and return the same pyo.Block as "
                f"model.{technology_name!r}, or set technology_parameters[{technology_name!r}] "
                f"to None when this technology cannot be built for the given data."
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
    def _electricity_energy_balance_rule(m, n, t):
        return m.electricity_sources[n, t] == m.electricity_sinks[n, t]

    model.electricity_balance = pyo.Constraint(
        model.NODES, model.T, rule=_electricity_energy_balance_rule
    )

    # -------------------------------------------------------------------------
    # Hydrogen balance: sources == sinks (per node, per time), LHV basis
    # All participating technologies use kWh-H2_LHV (lower heating value) per timestep.
    # Blocks that do not define hydrogen_source_term / hydrogen_sink_term are skipped.
    # With no hydrogen technologies registered, sources and sinks are both zero at each (node, t).
    # -------------------------------------------------------------------------
    def _hydrogen_sources_rule(m, n, t):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "hydrogen_source_term"):
                total += blk.hydrogen_source_term[n, t]
        return total

    model.hydrogen_sources = pyo.Expression(model.NODES, model.T, rule=_hydrogen_sources_rule)

    def _hydrogen_sinks_rule(m, n, t):
        total = 0.0
        for blk in m.component_objects(pyo.Block, descend_into=False):
            if hasattr(blk, "hydrogen_sink_term"):
                total += blk.hydrogen_sink_term[n, t]
        return total

    model.hydrogen_sinks = pyo.Expression(model.NODES, model.T, rule=_hydrogen_sinks_rule)

    def _hydrogen_balance_rule(m, n, t):
        return m.hydrogen_sources[n, t] == m.hydrogen_sinks[n, t]

    model.hydrogen_balance = pyo.Constraint(model.NODES, model.T, rule=_hydrogen_balance_rule)

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
    # These represent fixed costs that don't affect optimizaiton results but could affect reporting.
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
