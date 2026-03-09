"""Utility-specific loaders for OpenEI-style rate JSON.

The ``utility`` field in the JSON (normalized for lookup) routes to the right parser.
Each loader knows that utility's semantics: TOU, monthly tiered, daily tiered, flat, demand, etc.
Loader modules in this package are auto-discovered and registered on import.
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime

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


from data_loading.loaders.utility_rates.raw_timeseries import (
    RawEnergyPriceSeries,
    load_raw_energy_prices,
)


_register_loaders()


def import_prices_for_timestamps(rate: ParsedRate, timestamps: list[datetime]) -> list[float]:
    """Return import price ($/kWh) per timestamp using the rate and building-data timestamps.

    Use the timestamps from your building/load data so weekday/weekend and month match.
    Only TOU rates are supported; tiered/flat would need different logic.
    """
    if rate.rate_type != "tou":
        raise ValueError(f"import_prices_for_timestamps only supports rate_type='tou'; got {rate.rate_type!r}")
    from data_loading.loaders.utility_rates.sce import tou_import_prices_for_timestamps

    return tou_import_prices_for_timestamps(
        rate.payload["import_prices_12x24_weekday"],
        rate.payload["import_prices_12x24_weekend"],
        timestamps,
    )


def get_import_prices_for_timestamps(
    source: ParsedRate | RawEnergyPriceSeries,
    timestamps: list[datetime],
) -> list[float]:
    """Return the single import price ($/kWh) vector for the model utility block.

    Unified interface: accepts either an OpenEI ParsedRate (TOU) or a RawEnergyPriceSeries.
    Returns one list aligned to timestamps (length = len(timestamps)) for use in the cost function.
    """
    if isinstance(source, RawEnergyPriceSeries):
        prices = source.prices
        n = len(timestamps)
        if len(prices) == n:
            return list(prices)
        if len(prices) > n:
            return list(prices[:n])
        raise ValueError(f"Raw price series has {len(prices)} values but run has {n} periods; align length or use longer series")
    # ParsedRate (OpenEI TOU)
    return import_prices_for_timestamps(source, timestamps)


__all__ = [
    "BlockDirection",
    "RateType",
    "ParsedRate",
    "load_openei_rate",
    "register_utility",
    "get_loader",
    "import_prices_for_timestamps",
    "get_import_prices_for_timestamps",
    "RawEnergyPriceSeries",
    "load_raw_energy_prices",
]
