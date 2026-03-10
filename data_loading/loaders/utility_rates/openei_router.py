"""Router for OpenEI-style utility rate JSON.

Reads the JSON (file, string, or dict), uses the ``utility`` field to look up
the registered parser for that utility, and routes the rate item to it. Each
utility-specific module (sce, pge, etc.) registers its parser via @register_utility.
This module handles only traffic control and formatting of the OpenEI input;
the actual parsing is done by the utility-specific loader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Rate types the model can branch on.
RateType = Literal["tou", "monthly_tiered", "daily_tiered", "flat"]

# For tiered rates, block direction matters in the optimization model:
# - inclining: first block cheapest, later blocks more expensive (typical residential).
# - declining: first block most expensive, later blocks cheaper (e.g. some commercial).
# Utility loaders should set payload["block_direction"] = "inclining" | "declining"
# when rate_type is monthly_tiered or daily_tiered so the model can formulate cost correctly.
BlockDirection = Literal["inclining", "declining"]

# Registry: normalized utility name -> loader function.
# Each loader receives the single rate item dict and returns ParsedRate.
_REGISTRY: dict[str, Any] = {}


def _normalize_utility(name: str) -> str:
    """Normalize utility name for registry lookup: lowercase, collapse whitespace."""
    if not name or not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


def register_utility(utility_name: str):
    """Decorator to register a loader for a utility name (registered under normalized name)."""

    def decorator(fn: Any) -> Any:
        _REGISTRY[_normalize_utility(utility_name)] = fn
        return fn

    return decorator


def get_loader(utility_name: str) -> Any | None:
    """Return the registered loader for this utility (lookup by normalized name), or None."""
    return _REGISTRY.get(_normalize_utility(utility_name))


@dataclass
class ParsedRate:
    """Normalized rate after utility-specific parsing."""

    rate_type: RateType
    utility: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    """Type-specific data: e.g. hourly prices (tou), blocks + schedule (tiered), single rate (flat).
    For tiered rates, include payload["block_direction"] = "inclining" | "declining" when known,
    so the optimization model can handle declining block (first block dearest) differently from
    inclining block (first block cheapest)."""
    demand_charges: dict[str, Any] | None = None
    """Optional demand-charge component for model.utility block. When present, structure:
    demand_charge_type: "flat" | "tou" | "both" – which demand-charge components apply.
    TOU demand charge (if demand_charge_type in ("tou","both")):
      demand_charge_ratestructure, demand_charge_weekdayschedule, demand_charge_weekendschedule.
      Schedules are 12×24: schedule[month][hour] = tier index into demand_charge_ratestructure.
    Flat demand charge (if demand_charge_type in ("flat","both")):
      flat_demand_charge_structure, flat_demand_charge_months, flat_demand_charge_applicable_months.
      flat_demand_charge_applicable_months: list[int] month indices 0–11 where flat demand charge applies.
    Model resolves which hours fall into which demand-charge periods from its time series."""


def load_openei_rate(
    path_or_json: Path | str | dict,
    *,
    item_index: int | None = None,
) -> ParsedRate:
    """Load an OpenEI rate JSON and route to the correct utility parser.

    Args:
        path_or_json: Path to a JSON file, a JSON string (must start with '{' or '['),
            or a parsed dict (e.g. full ``{"items": [...]}`` or a single rate item).
        item_index: If the response has ``items``, which item to use (0-based).
            Default 0. Use when the OpenEI response contains multiple tariffs.

    Returns:
        ParsedRate with rate_type and payload filled by the utility loader.

    Raises:
        KeyError: If the utility is not in the registry.
        ValueError: If the input has no items, no utility field, or item_index out of range.
    """
    #This chunk of code is used to load the OpenEI rate JSON from a file, a string, or a dictionary
    if isinstance(path_or_json, dict): #If its a dictionary, set the data to the path_or_json
        data = path_or_json
    elif isinstance(path_or_json, str): #If its a string, strip it and check if it starts with { or [   
        s = path_or_json.strip()
        if s.startswith("{") or s.startswith("["):
            data = json.loads(path_or_json) #If it starts with { or [, load the json
        else:
            data = json.loads(Path(path_or_json).read_text(encoding="utf-8")) #If it doesn't start with { or [, load the json from the file
    elif isinstance(path_or_json, Path):
        data = json.loads(path_or_json.read_text(encoding="utf-8")) #If its a path, load the json from the file
    else:
        raise TypeError("path_or_json must be Path, str, or dict") #If its not a path, string, or dictionary, raise an error

    # Accept full OpenEI response, top-level list of items, or single item
    if isinstance(data, list):
        items = data
        if not items:
            raise ValueError("OpenEI rate JSON is an empty list; need at least one rate item.")
        idx = item_index if item_index is not None else 0
        if idx < 0 or idx >= len(items):
            raise ValueError(
                f"item_index {idx} out of range (response has {len(items)} item(s)). "
                "Use item_index=0, ... for the first tariff."
            )
        item = items[idx]
    elif isinstance(data, dict) and "items" in data:
        items = data["items"]
        if not items:
            raise ValueError("OpenEI rate JSON has no items")
        idx = item_index if item_index is not None else 0
        if idx < 0 or idx >= len(items):
            raise ValueError(
                f"item_index {idx} out of range (response has {len(items)} item(s)). "
                "Use item_index=0, ... for the first tariff."
            )
        item = items[idx]
    elif isinstance(data, dict):
        item = data
    else:
        raise ValueError(
            f"Parsed JSON must be an object (with optional 'items' key), a list of rate items, or a single rate object; got {type(data).__name__}."
        )

    if not isinstance(item, dict):
        raise ValueError(
            f"Selected rate item must be a JSON object; got {type(item).__name__}. "
            "Check that the list or 'items' array contains rate objects."
        )

    utility = item.get("utility")
    if not utility:
        raise ValueError("Rate item has no 'utility' field")

    loader = get_loader(utility)
    if loader is None:
        raise KeyError(
            f"No loader registered for utility {utility!r}. "
            f"Registered (normalized): {list(_REGISTRY.keys())}"
        )

    # Validate loader return at the boundary so bad data never propagates; fail with a clear contract error.
    result = loader(item)
    if result is None:
        raise TypeError(f"Loader for {utility!r} returned None; must return ParsedRate")
    if not isinstance(result, ParsedRate):
        raise TypeError(f"Loader for {utility!r} returned {type(result).__name__}, expected ParsedRate")
    if not hasattr(result, "rate_type") or not hasattr(result, "utility") or not hasattr(result, "name"):
        raise ValueError(f"Loader for {utility!r} returned ParsedRate missing rate_type/utility/name")
    return result
