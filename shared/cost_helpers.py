"""Cost-expression helpers for technology blocks.

Standardizes the four cost patterns repeated across tech blocks:

1. Annualized fixed cost over ``[node]`` (capital or fixed O&M).
2. Annualized fixed cost over ``[node, category]`` (solar profiles, hydrokinetic techs).
3. Time-summed variable cost (variable O&M, fuel) with optional efficiency divisor.
4. Orchestration of standard cost-component names on the block, incl. the
   ``if allow_adoption else 0.0`` boilerplate and assembly of
   ``objective_contribution`` / ``cost_non_optimizing_annual`` (required by
   ``model/contracts.py``).

Standard component names attached by ``attach_standard_cost_expressions``:

- ``annual_capital_costs``: 0.0 when the tech is not allowed to adopt.
- ``fixed_om_adopted``: 0.0 when the tech is not allowed to adopt.
- ``fixed_om_existing``: annual fixed O&M on existing (pre-installed) capacity.
- ``variable_operating_cost``: per-timestep variable O&M and/or fuel (0.0 if absent).
- ``objective_contribution``: sum of the above plus any ``extra_objective_terms``.
- ``cost_non_optimizing_annual``: equals ``fixed_om_existing`` (reporting-only hook
  already honored by ``model.core``).

The helpers do not require specific Param/Var names on the block, only that the
caller passes the right component for each role.
"""

from __future__ import annotations

from typing import Iterable

import pyomo.environ as pyo


def annualized_fixed_cost_by_node(
    *,
    cost_per_unit,
    capacity_var,
    nodes,
    amortization_factor=1.0,
) -> pyo.Expression:
    """Return ``Expression`` for ``sum_n cost_per_unit * capacity_var[n] * amortization_factor``.

    Covers both annualized capital (pass ``amortization_factor=block.amortization_factor``)
    and annual fixed O&M (leave ``amortization_factor`` at its default of 1.0).

    Args:
        cost_per_unit: Scalar Param, e.g. ``capital_cost_per_kw`` or ``fixed_om_per_kw_year``.
        capacity_var: Var or Expression indexed by ``[node]``, e.g. adopted or existing capacity.
        nodes: Iterable of node keys to sum over.
        amortization_factor: Scalar Param for annualized capital, or 1.0 for fixed O&M.
    """
    return pyo.Expression(
        expr=sum(
            cost_per_unit * capacity_var[n] * amortization_factor for n in nodes
        )
    )


def annualized_fixed_cost_by_node_category(
    *,
    cost_per_unit_by_category,
    capacity_var_by_node_category,
    nodes,
    categories,
    amortization_factor=1.0,
) -> pyo.Expression:
    """Two-dimensional variant: ``sum_{n,c} cost[c] * capacity[n,c] * amortization_factor``.

    Used by technologies with an extra index dimension: solar profiles, hydrokinetic
    turbine types, or any per-category cost basis.

    Args:
        cost_per_unit_by_category: Param indexed by ``[category]`` (e.g. solar profile).
        capacity_var_by_node_category: Var or Expression indexed by ``[node, category]``.
        nodes: Iterable of node keys.
        categories: Iterable of category keys (profile IDs, turbine types, etc.).
        amortization_factor: Scalar Param for capital, or 1.0 for fixed O&M.
    """
    return pyo.Expression(
        expr=sum(
            cost_per_unit_by_category[c]
            * capacity_var_by_node_category[n, c]
            * amortization_factor
            for n in nodes
            for c in categories
        )
    )


def time_summed_variable_cost(
    *,
    cost_per_unit,
    flow_var,
    nodes,
    time_set,
    dt_hours=1.0,
    efficiency_divisor=1.0,
) -> pyo.Expression:
    """Return ``Expression`` for ``sum_{n,t} (cost_per_unit / efficiency_divisor) * flow_var[n,t] * dt_hours``.

    Covers variable O&M (default ``efficiency_divisor=1.0``) and fuel cost (pass the
    electric-generation-to-fuel-input conversion as ``efficiency_divisor``, e.g. diesel's
    ``electric_efficiency``).

    Args:
        cost_per_unit: Scalar Param, e.g. ``variable_om_per_kwh`` or ``fuel_cost_per_kwh_diesel``.
        flow_var: Var indexed by ``[node, t]``, typically generation or consumption.
        nodes: Iterable of node keys.
        time_set: Iterable of time indices.
        dt_hours: Float or scalar Param; converts per-timestep kW to kWh when the flow
            variable is in kW. If ``flow_var`` is already per-timestep energy, pass 1.0.
        efficiency_divisor: Scalar Param or float. Use to convert electric output to fuel
            input (e.g. ``electric_efficiency`` for a diesel generator). Default 1.0.
    """
    return pyo.Expression(
        expr=sum(
            (cost_per_unit / efficiency_divisor) * flow_var[n, t] * dt_hours
            for n in nodes
            for t in time_set
        )
    )


def attach_standard_cost_expressions(
    block: pyo.Block,
    *,
    allow_adoption: bool,
    fixed_om_existing: pyo.Expression,
    annualized_capital_if_adopted: pyo.Expression | None = None,
    fixed_om_adopted_if_adopted: pyo.Expression | None = None,
    variable_operating_cost: pyo.Expression | None = None,
    extra_objective_terms: Iterable[pyo.Expression] = (),
) -> None:
    """Attach standardized cost components to ``block``.

    After this call, ``block`` has the following components (required by
    ``model/contracts.py`` or used for downstream reporting):

    - ``annual_capital_costs``: the ``annualized_capital_if_adopted`` Expression if
      ``allow_adoption`` is True and one was provided, else a scalar Expression = 0.0.
    - ``fixed_om_adopted``: the ``fixed_om_adopted_if_adopted`` Expression if
      ``allow_adoption`` is True and one was provided, else = 0.0.
    - ``fixed_om_existing``: the passed-in existing-capacity fixed O&M Expression.
    - ``variable_operating_cost``: the passed-in Expression, else = 0.0.
    - ``objective_contribution``: scalar sum of annual_capital_costs +
      fixed_om_adopted + variable_operating_cost + sum(extra_objective_terms).
    - ``cost_non_optimizing_annual``: = ``fixed_om_existing`` (reporting-only).

    Args:
        block: Tech block to attach components to.
        allow_adoption: If False, capital and adopted-O&M components are 0.0.
        fixed_om_existing: Required; O&M on existing (already-installed) capacity.
        annualized_capital_if_adopted: Capital cost expression used when
            ``allow_adoption`` is True.
        fixed_om_adopted_if_adopted: Adopted-capacity O&M expression used when
            ``allow_adoption`` is True.
        variable_operating_cost: Per-timestep variable O&M and/or fuel cost; omitted
            for pure storage technologies.
        extra_objective_terms: Additional cost expressions to fold into
            ``objective_contribution`` (e.g. a future fixed adoption charge or
            piecewise capital term).
    """
    if allow_adoption and annualized_capital_if_adopted is not None:
        block.annual_capital_costs = annualized_capital_if_adopted
    else:
        block.annual_capital_costs = pyo.Expression(expr=0.0)

    if allow_adoption and fixed_om_adopted_if_adopted is not None:
        block.fixed_om_adopted = fixed_om_adopted_if_adopted
    else:
        block.fixed_om_adopted = pyo.Expression(expr=0.0)

    block.fixed_om_existing = fixed_om_existing

    if variable_operating_cost is not None:
        block.variable_operating_cost = variable_operating_cost
    else:
        block.variable_operating_cost = pyo.Expression(expr=0.0)

    block.objective_contribution = pyo.Expression(
        expr=(
            block.annual_capital_costs
            + block.fixed_om_adopted
            + block.variable_operating_cost
            + sum(extra_objective_terms)
        )
    )
    block.cost_non_optimizing_annual = pyo.Expression(expr=block.fixed_om_existing)
