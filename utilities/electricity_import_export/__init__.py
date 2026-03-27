"""Electricity import/export utility package.

Provides the grid import variable, node-specific energy cost, and node-specific demand charges from
``ParsedRate.demand_charges`` (flat and TOU). Utility-specific loaders normalize tariffs into
``ParsedRate`` so the block does not branch on utility names.

Layout:

- ``inputs`` — grouped sections: energy prices ($/kWh), tariffs, demand-charge prerequisites, fixed fees.
- ``demand_charge_indexing`` — demand-only: calendar buckets, flat vs TOU indexing (pure Python).
- ``block`` — Pyomo block with sections: grid import, demand kW proxy, energy charges, demand charges, objective split.
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
