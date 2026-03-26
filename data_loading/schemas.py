"""Shared schema placeholders for model input containers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DataContainer:
    """Unified model input container: loads, renewables (solar), and utility data.

    indices/timeseries/static: load and resource series (solar, etc.) and time index.
    import_prices: resolved $/kWh per period (from OpenEI or raw 8760/N), aligned to container time.
    utility_rate: optional ParsedRate for demand charges/metadata when grid block exists.
    """

    indices: dict[str, Any] = field(default_factory=dict)
    timeseries: dict[str, Any] = field(default_factory=dict)
    static: dict[str, Any] = field(default_factory=dict)
    # Utility: single import price vector and optional rate metadata (demand charges, etc.)
    import_prices: list[float] | None = None
    utility_rate: Any = None
    # Utility (optional per-node extension):
    # node = customer/meter assumption for utility billing.
    import_prices_by_node: dict[str, list[float]] | None = None
    utility_rate_by_node: dict[str, Any] | None = None
    node_utility_tariff_key: dict[str, str] | None = None

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

