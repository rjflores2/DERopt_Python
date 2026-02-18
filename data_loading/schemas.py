"""Shared schema placeholders for model input containers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DataContainer:
    """Unified model input container used by core and module blocks."""

    indices: dict[str, Any] = field(default_factory=dict)
    timeseries: dict[str, Any] = field(default_factory=dict)
    static: dict[str, Any] = field(default_factory=dict)
    tech_params: dict[str, Any] = field(default_factory=dict)

