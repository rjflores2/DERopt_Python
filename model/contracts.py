"""Explicit contract for technology blocks attached from ``technologies.REGISTRY``.

``model.core`` balances electricity and hydrogen by summing optional
``electricity_*_term`` / ``hydrogen_*_term`` on each top-level ``Block``, and sums
``objective_contribution`` / ``cost_non_optimizing_annual`` for cost reporting.

This module validates **registered** technology blocks after ``model.core`` confirms
``register()`` attached the returned Block as ``model.<technology_name>`` (see ``build_model``).

The utility block is not a registry technology and is not validated here.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

# Names core assembly expects (see ``model.core`` balance and objective rules).
TECH_OBJECTIVE_CONTRIBUTION = "objective_contribution"
TECH_COST_NON_OPTIMIZING_ANNUAL = "cost_non_optimizing_annual"
TECH_ELECTRICITY_SOURCE_TERM = "electricity_source_term"
TECH_ELECTRICITY_SINK_TERM = "electricity_sink_term"
TECH_HYDROGEN_SOURCE_TERM = "hydrogen_source_term"
TECH_HYDROGEN_SINK_TERM = "hydrogen_sink_term"

_INDEXED_BALANCE_ATTRS = (
    TECH_ELECTRICITY_SOURCE_TERM,
    TECH_ELECTRICITY_SINK_TERM,
    TECH_HYDROGEN_SOURCE_TERM,
    TECH_HYDROGEN_SINK_TERM,
)


def _is_indexed_component(comp: Any) -> bool:
    return hasattr(comp, "is_indexed") and bool(comp.is_indexed())


def _require_scalar_cost_component(technology_key: str, block: pyo.Block, attr: str) -> None:
    """``objective_contribution`` and optional ``cost_non_optimizing_annual`` must be scalar."""
    if not hasattr(block, attr):
        if attr == TECH_OBJECTIVE_CONTRIBUTION:
            raise ValueError(
                f"technology {technology_key!r}: technology block must define "
                f"{TECH_OBJECTIVE_CONTRIBUTION!r} (scalar Pyomo Expression)."
            )
        return
    comp = getattr(block, attr)
    if _is_indexed_component(comp):
        raise ValueError(
            f"technology {technology_key!r}: {attr!r} must be a scalar expression, "
            f"not indexed over time or nodes."
        )


def _validate_indexed_nt(
    technology_key: str,
    block: pyo.Block,
    attr: str,
    model: pyo.ConcreteModel,
) -> None:
    """Balance terms must support ``[node, t]`` for every pair in ``model.NODES`` and ``model.T``.

    This is intentionally weaker than requiring ``component.index_set() == model.NODES * model.T``,
    so aliases, ``Reference``, or equivalent index constructions still pass as long as the
    electricity/hydrogen balance in ``model.core`` can subscript them the same way.
    """
    comp = getattr(block, attr)
    if not _is_indexed_component(comp):
        raise ValueError(
            f"technology {technology_key!r}: {attr!r} must be an indexed Expression (or Var) "
            f"over (node, time) to match electricity/hydrogen balance in model.core."
        )
    nodes = list(model.NODES)
    times = list(model.T)
    for n in nodes:
        for t in times:
            try:
                comp[n, t]
            except Exception as exc:
                raise ValueError(
                    f"technology {technology_key!r}: {attr!r} must be subscriptable at every "
                    f"(node, time) in model.NODES x model.T; failed at ({n!r}, {t!r}). "
                    f"Underlying error: {exc}"
                ) from exc


def validate_technology_block_interface(
    *,
    technology_key: str,
    block: pyo.Block,
    model: pyo.ConcreteModel,
) -> None:
    """Raise ``ValueError`` if ``block`` does not satisfy the registry technology contract.

    Required:
        - ``objective_contribution``: scalar (non-indexed) optimizing cost term.

    Optional (validated when present):
        - ``cost_non_optimizing_annual``: scalar reporting-only cost.
        - ``electricity_source_term`` / ``electricity_sink_term``: indexed ``[node, t]``.
        - ``hydrogen_source_term`` / ``hydrogen_sink_term``: indexed ``[node, t]``.

    A technology may define only electricity terms, only hydrogen terms, both, or neither
    (e.g. hypothetical pure-capacity placeholder), as long as objective exists and any
    present balance term is correctly indexed.
    """
    if not hasattr(model, "NODES") or not hasattr(model, "T"):
        raise RuntimeError(
            "validate_technology_block_interface requires model.NODES and model.T (internal error)."
        )

    _require_scalar_cost_component(technology_key, block, TECH_OBJECTIVE_CONTRIBUTION)
    _require_scalar_cost_component(technology_key, block, TECH_COST_NON_OPTIMIZING_ANNUAL)

    for attr in _INDEXED_BALANCE_ATTRS:
        if hasattr(block, attr):
            _validate_indexed_nt(technology_key, block, attr, model)
