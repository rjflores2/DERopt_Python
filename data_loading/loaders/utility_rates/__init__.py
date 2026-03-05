"""Utility-specific loaders for OpenEI-style rate JSON.

Dispatch is by the ``utility`` field in the JSON. Each loader knows that
utility's semantics: TOU, monthly tiered, daily tiered, flat, demand, etc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Rate types the model can branch on.
RateType = Literal["tou", "monthly_tiered", "daily_tiered", "flat"]

# Registry: utility name (exact string from OpenEI) -> loader function.
# Each loader receives the single rate item dict and returns ParsedRate.
_REGISTRY: dict[str, Any] = {}


def register_utility(utility_name: str):
    """Decorator to register a loader for a utility name."""

    def decorator(fn: Any) -> Any:
        _REGISTRY[utility_name] = fn
        return fn

    return decorator


def get_loader(utility_name: str) -> Any | None:
    """Return the registered loader for this utility, or None."""
    return _REGISTRY.get(utility_name)


@dataclass
class ParsedRate:
    """Normalized rate after utility-specific parsing."""

    rate_type: RateType
    utility: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    """Type-specific data: e.g. hourly prices (tou), blocks + schedule (tiered), single rate (flat)."""
    demand: dict[str, Any] | None = None
    """Optional demand component (e.g. $/kW by period, flat demand)."""


def load_openei_rate(path_or_json: Path | str | dict) -> ParsedRate:
    """Load an OpenEI rate file and dispatch to the utility-specific loader.

    Args:
        path_or_json: Path to a JSON file, or a JSON string, or the parsed
            dict (e.g. full ``{"items": [...]}`` or a single rate item).

    Returns:
        ParsedRate with rate_type and payload filled by the utility loader.

    Raises:
        KeyError: If the utility is not in the registry.
        ValueError: If the input has no items or no utility field.
    """
    if isinstance(path_or_json, dict):
        data = path_or_json
    elif isinstance(path_or_json, (Path, str)):
        path = Path(path_or_json)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise TypeError("path_or_json must be Path, str, or dict")

    # Accept full OpenEI response or single item
    if "items" in data:
        items = data["items"]
        if not items:
            raise ValueError("OpenEI rate JSON has no items")
        item = items[0]
    else:
        item = data

    utility = item.get("utility")
    if not utility:
        raise ValueError("Rate item has no 'utility' field")

    loader = get_loader(utility)
    if loader is None:
        raise KeyError(
            f"No loader registered for utility {utility!r}. "
            f"Registered: {list(_REGISTRY.keys())}"
        )

    return loader(item)


# Register utility-specific loaders (must import to run decorators).
from data_loading.loaders.utility_rates import sce  # noqa: E402, F401

__all__ = [
    "RateType",
    "ParsedRate",
    "load_openei_rate",
    "register_utility",
    "get_loader",
]
