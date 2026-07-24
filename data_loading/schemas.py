"""Shared schema placeholders for model input containers."""

from dataclasses import dataclass, field
from typing import Any

from shared.scenario_helpers import validate_scenario_probabilities


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
    # Values may share the same list object across nodes when multiple nodes use the same tariff
    # (memory-efficient; treat as read-only after load).
    import_prices_by_node: dict[str, list[float]] | None = None
    utility_rate_by_node: dict[str, Any] | None = None
    node_utility_tariff_key: dict[str, str] | None = None

    # Stochastic scenarios (two-stage): default is a single implicit scenario at
    # probability 1.0, so a case with no scenarios configured behaves exactly like
    # today's deterministic model. scenario_* override dicts are sparse: a
    # (scenario_key, series_key) pair absent means that scenario uses the base
    # timeseries/import_prices_by_node value (deliberate broadcast). See
    # shared/scenario_helpers.py for the lookup-with-fallback helpers.
    scenario_keys: list[str] = field(default_factory=lambda: ["_default"])
    scenario_probability: dict[str, float] = field(default_factory=lambda: {"_default": 1.0})
    scenario_timeseries: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    scenario_import_prices_by_node: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    scenario_utility_rate_by_node: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Present only for a scenario_key that loaded its own hydrokinetic_path with a reference_kw/
    # reference_swept_area_m2 that differs from the base case (see technologies.hydrokinetic.block,
    # which needs the SAME reference_kw used to normalize a series at load time to correctly
    # de-normalize it back to kWh/m^2 at build time -- these two dicts carry that pairing through
    # the sparse-override, same as scenario_timeseries carries the series itself).
    scenario_hydrokinetic_reference_kw: dict[str, float] = field(default_factory=dict)
    scenario_hydrokinetic_reference_swept_area_m2: dict[str, float] = field(default_factory=dict)

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
        self.validate_scenarios()

    def validate_scenarios(self) -> None:
        """Fail fast on invalid scenario_keys/scenario_probability.

        Public (not the earlier ``_validate_scenarios``) because ``model.core.build_model``
        calls this directly as its own defense-in-depth check, rather than hand-duplicating
        the same arithmetic -- see ``shared.scenario_helpers.validate_scenario_probabilities``,
        the single shared implementation both this method and ``run.build_run_data``'s
        config-layer validation delegate to.
        """
        validate_scenario_probabilities(self.scenario_keys, self.scenario_probability)

