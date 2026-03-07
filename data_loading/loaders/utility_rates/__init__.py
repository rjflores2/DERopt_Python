"""Utility-specific loaders for OpenEI-style rate JSON.

The ``utility`` field in the JSON (normalized for lookup) routes to the right parser.
Each loader knows that utility's semantics: TOU, monthly tiered, daily tiered, flat, demand, etc.
Loader modules in this package are auto-discovered and registered on import.
"""

from __future__ import annotations

import importlib
import pkgutil

from data_loading.loaders.utility_rates.openei_router import (
    BlockDirection,
    ParsedRate,
    RateType,
    get_loader,
    load_openei_rate,
    register_utility,
)

# Auto-discover and import loader modules (sce, pge, etc.) so @register_utility runs.
# Skip openei_router so only actual utility parser modules are imported here.
def _register_loaders() -> None:
    pkg = __package__
    if pkg is None:
        return
    for _importer, modname, _ispkg in pkgutil.iter_modules(__path__):
        if modname not in ("__init__", "openei_router"):
            importlib.import_module(f".{modname}", pkg)


_register_loaders()

__all__ = [
    "BlockDirection",
    "RateType",
    "ParsedRate",
    "load_openei_rate",
    "register_utility",
    "get_loader",
]
