"""Electricity import/export utility package.

Provides the grid import variable, node-specific energy cost, and node-specific demand charges from
``ParsedRate.demand_charges`` (flat and TOU). Utility-specific loaders normalize tariffs into
``ParsedRate`` so the block does not branch on utility names.

Layout:

- ``inputs`` — resolve node-scoped prices/rates, validate demand-charge prerequisites, fixed charges.
- ``demand_charge_indexing`` — calendar buckets and TOU tier indexing (pure Python).
- ``block`` — Pyomo variables, constraints, and cost expressions.
- ``diagnostics`` — reserved; see ``utilities.model_diagnostics`` for tariff/import warnings.

The grid/utility block is not part of ``technologies.REGISTRY``; ``model.core`` attaches it via
``register`` when energy prices, demand charges, or fixed customer charges apply.
"""

from __future__ import annotations

from .block import add_utility_block, register

__all__ = [
    "add_utility_block",
    "register",
]
