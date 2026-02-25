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

    def validate_minimum_fields(self) -> None:
        """Validate minimum fields required by early slices."""
        if "time" not in self.indices:
            raise ValueError("indices.time is required")
        if "time_serial" not in self.timeseries:
            raise ValueError("timeseries.time_serial is required")
        keys = self.static.get("electricity_load_keys") or []
        if not keys:
            raise ValueError("static.electricity_load_keys is required (non-empty)")
        for key in keys:
            if key not in self.timeseries:
                raise ValueError(f"timeseries.{key} is required")

