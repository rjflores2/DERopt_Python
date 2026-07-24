"""Build the unified run data container from case config.

Loads energy load, solar, hydrokinetic, utility (OpenEI or raw 8760/N), and populates a single
DataContainer. Add wind, export rates, post-processing here so playground
stays a thin entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from data_loading.loaders import (
    load_energy_load,
    load_hydrokinetic_into_container,
    load_openei_rate,
    load_solar_into_container,
    load_wind_into_container,
)
from data_loading.loaders.utility_rates import (
    get_import_prices_for_timestamps,
    load_raw_energy_prices,
)
from data_loading.schemas import DataContainer
from data_loading.time_subset import apply_time_subset
from shared.scenario_helpers import validate_scenario_probabilities

from config.case_config import UtilityGridMode

if TYPE_CHECKING:
    from config.case_config import CaseConfig, ScenarioConfig, UtilityTariffConfig


# ---------------------------------------------------------------------------
# Low-level helpers: price alignment and tariff source resolution
# ---------------------------------------------------------------------------


def _align_raw_prices_to_periods(prices: list[float], n: int) -> list[float]:
    """Align a raw price series to the run horizon of ``n`` periods.

    Returns the first ``n`` entries if longer, the list as-is if exact,
    or raises if shorter (ambiguous how to extend).
    """
    if len(prices) == n:
        return list(prices)
    if len(prices) > n:
        return list(prices[:n])
    raise ValueError(
        f"Raw price series has {len(prices)} values but run has {n} periods; align length or use longer series"
    )


def _resolve_tariff_source(
    *,
    utility_rate_path: Path | None,
    utility_rate_item_index: int | None,
    energy_price_path: Path | None,
    energy_price_column: str | None,
    timestamps: list[Any],
    n_periods: int,
) -> tuple[Any | None, list[float] | None]:
    """Load a single tariff's OpenEI rate and/or raw energy prices into
    ``(utility_rate, import_prices)``.

    - Raw prices, when present, override OpenEI energy prices.
    - TOU OpenEI energy prices require datetimes on the run horizon.
    - Demand charges on the OpenEI rate require datetimes on the run horizon.
    """
    utility_rate = None
    if utility_rate_path is not None:
        if not utility_rate_path.exists():
            raise FileNotFoundError(f"utility_rate_path set but file missing: {utility_rate_path}")
        utility_rate = load_openei_rate(
            utility_rate_path,
            item_index=utility_rate_item_index,
        )

    raw_prices = None
    if energy_price_path is not None:
        if not energy_price_path.exists():
            raise FileNotFoundError(f"energy_price_path set but file missing: {energy_price_path}")
        raw_prices = load_raw_energy_prices(
            energy_price_path,
            price_column=energy_price_column,
        )

    has_demand_charges = bool(
        utility_rate is not None
        and getattr(utility_rate, "demand_charges", None)
        and getattr(utility_rate, "demand_charges", {}).get("demand_charge_type")
    )
    if has_demand_charges and not timestamps:
        raise ValueError(
            "utility_rate includes demand charges but load data has no timeseries['datetime']. "
            "Demand-charge billing windows are month/weekday/weekend/hour based and require timestamps."
        )

    import_prices = None
    if raw_prices is not None:
        # Raw prices override OpenEI energy rates.
        if timestamps:
            import_prices = get_import_prices_for_timestamps(raw_prices, timestamps)
        else:
            import_prices = _align_raw_prices_to_periods(raw_prices.prices, n_periods)
    elif utility_rate is not None:
        # OpenEI TOU energy prices require calendar timestamps to map weekday/weekend and month/hour schedules.
        if getattr(utility_rate, "rate_type", None) == "tou":
            if not timestamps:
                raise ValueError(
                    "utility_rate provided a TOU tariff but load data has no timeseries['datetime']. "
                    "TOU energy prices require timestamps to map the schedule. "
                    "Fix the load file / loader so datetimes are present, or set energy_price_path to provide an explicit per-period import price vector."
                )
            import_prices = get_import_prices_for_timestamps(utility_rate, timestamps)

    return utility_rate, import_prices


# ---------------------------------------------------------------------------
# Resource loaders (non-utility): solar, hydrokinetic
# ---------------------------------------------------------------------------


def _load_resources(data: DataContainer, case_cfg: CaseConfig) -> None:
    """Load optional renewable / hydrokinetic resources into ``data`` in place."""
    if case_cfg.solar_path is not None:
        if not case_cfg.solar_path.exists():
            raise FileNotFoundError(f"solar_path set but file missing: {case_cfg.solar_path}")
        load_solar_into_container(data, case_cfg.solar_path)

    wind_path = getattr(case_cfg, "wind_path", None)
    if wind_path is not None:
        if not wind_path.exists():
            raise FileNotFoundError(f"wind_path set but file missing: {wind_path}")
        load_wind_into_container(data, wind_path)

    hkt_path = getattr(case_cfg, "hydrokinetic_path", None)
    if hkt_path is not None:
        if not hkt_path.exists():
            raise FileNotFoundError(f"hydrokinetic_path set but file missing: {hkt_path}")
        load_hydrokinetic_into_container(
            data,
            hkt_path,
            reference_kw=getattr(case_cfg, "hydrokinetic_reference_kw", 1.0),
            datetime_column=getattr(case_cfg, "hydrokinetic_datetime_column", None),
            reference_swept_area_m2=getattr(case_cfg, "hydrokinetic_reference_swept_area_m2", None),
        )


# ---------------------------------------------------------------------------
# Utility loaders: single-tariff legacy path and multi-tariff path
# ---------------------------------------------------------------------------


def _utility_mode(case_cfg: Any) -> UtilityGridMode:
    """Return validated ``utility_mode`` (defaults to priced_grid for plain namespaces)."""
    raw = getattr(case_cfg, "utility_mode", "priced_grid")
    allowed: tuple[UtilityGridMode, ...] = ("priced_grid", "free_grid", "islanded")
    if raw not in allowed:
        raise ValueError(
            f"utility_mode must be one of {list(allowed)}; got {raw!r}."
        )
    return raw


def _validate_utility_mode_config(case_cfg: Any) -> None:
    """Fail fast on contradictory or underspecified grid/utility configuration."""
    mode = _utility_mode(case_cfg)
    utility_tariffs = getattr(case_cfg, "utility_tariffs", None)
    has_multi = utility_tariffs is not None
    urp = getattr(case_cfg, "utility_rate_path", None)
    epp = getattr(case_cfg, "energy_price_path", None)
    has_single = urp is not None or epp is not None

    if has_multi:
        if mode == "islanded":
            raise ValueError(
                "utility_mode='islanded' cannot be combined with utility_tariffs "
                "(multi-tariff runs require priced grid). Remove utility_tariffs or set "
                "utility_mode='priced_grid'."
            )
        if mode == "free_grid":
            raise ValueError(
                "utility_mode='free_grid' cannot be combined with utility_tariffs. "
                "Remove utility_tariffs or set utility_mode='priced_grid'."
            )
        if mode == "priced_grid":
            for tcfg in utility_tariffs or []:
                if tcfg.utility_rate_path is None and tcfg.energy_price_path is None:
                    raise ValueError(
                        f"utility_tariffs entry {tcfg.tariff_key!r} must set utility_rate_path "
                        "and/or energy_price_path when utility_mode='priced_grid'."
                    )
    else:
        if mode == "priced_grid" and not has_single:
            raise ValueError(
                "utility_mode='priced_grid' requires a utility price source: set "
                "utility_rate_path, energy_price_path, or utility_tariffs on CaseConfig. "
                "For intentional zero marginal cost grid imports, set utility_mode='free_grid'. "
                "For no grid connection, set utility_mode='islanded'."
            )
        if mode == "free_grid" and has_single:
            raise ValueError(
                "utility_mode='free_grid' cannot be combined with utility_rate_path or "
                "energy_price_path (contradictory). Remove those paths or use "
                "utility_mode='priced_grid'."
            )
        if mode == "islanded" and has_single:
            raise ValueError(
                "utility_mode='islanded' cannot be combined with utility_rate_path or "
                "energy_price_path. Remove those paths or use utility_mode='priced_grid'."
            )


def _load_utilities_single(
    data: DataContainer,
    case_cfg: CaseConfig,
    *,
    timestamps: list[Any],
    n_periods: int,
) -> None:
    """Populate ``data.import_prices_by_node`` / ``utility_rate_by_node`` from the legacy
    single-tariff fields on ``case_cfg`` (``utility_rate_path``, ``energy_price_path``, ...).
    """
    mode = _utility_mode(case_cfg)
    nodes = list(data.static.get("electricity_load_keys") or [])
    if not nodes:
        raise ValueError(
            "single-tariff utility setup requires non-empty data.static['electricity_load_keys']."
        )

    if mode == "islanded":
        data.import_prices_by_node = None
        data.utility_rate_by_node = None
        data.node_utility_tariff_key = None
        return

    if mode == "free_grid":
        zero_prices = [0.0] * n_periods
        data.import_prices_by_node = {node: zero_prices for node in nodes}
        data.utility_rate_by_node = {node: None for node in nodes}
        data.node_utility_tariff_key = {node: "default" for node in nodes}
        return

    utility_rate, import_prices = _resolve_tariff_source(
        utility_rate_path=getattr(case_cfg, "utility_rate_path", None),
        utility_rate_item_index=getattr(case_cfg, "utility_rate_item_index", None),
        energy_price_path=getattr(case_cfg, "energy_price_path", None),
        energy_price_column=getattr(case_cfg, "energy_price_column", None),
        timestamps=timestamps,
        n_periods=n_periods,
    )

    # Share one zero vector across nodes when no energy prices resolved but a tariff/rate
    # object exists (e.g. demand-only or non-TOU OpenEI); memory: O(T), not O(nodes*T).
    zero_prices = [0.0] * n_periods
    node_prices = import_prices if import_prices is not None else zero_prices
    data.import_prices_by_node = {node: node_prices for node in nodes}
    data.utility_rate_by_node = {node: utility_rate for node in nodes}
    data.node_utility_tariff_key = {node: "default" for node in nodes}


def _load_utilities_multi(
    data: DataContainer,
    case_cfg: CaseConfig,
    utility_tariffs: list[UtilityTariffConfig],
    *,
    timestamps: list[Any],
    n_periods: int,
) -> None:
    """Populate per-node utility data from the multi-tariff config.

    - ``utility_tariffs`` is authoritative: legacy single-tariff fields on ``case_cfg`` must be unset.
    - The first tariff is the default; ``case_cfg.node_utility_tariff`` overrides per node.
    - Identical tariff specs are loaded once and shared across nodes (price list is the same object).
    """
    # Guard: legacy single-tariff fields must not be combined with the multi-tariff config.
    if (
        getattr(case_cfg, "utility_rate_path", None) is not None
        or getattr(case_cfg, "energy_price_path", None) is not None
        or getattr(case_cfg, "utility_rate_item_index", None) is not None
        or getattr(case_cfg, "energy_price_column", None) is not None
    ):
        raise ValueError(
            "When utility_tariffs is provided, legacy single-tariff fields "
            "(utility_rate_path, utility_rate_item_index, energy_price_path, energy_price_column) "
            "must be unset."
        )

    if not utility_tariffs:
        raise ValueError("utility_tariffs must contain at least one tariff entry.")

    nodes = list(data.static.get("electricity_load_keys") or [])
    if not nodes:
        raise ValueError("utility_tariffs requires non-empty data.static['electricity_load_keys'].")

    default_key = utility_tariffs[0].tariff_key
    if not default_key:
        raise ValueError("utility_tariffs[0].tariff_key must be non-empty (default tariff).")

    # Validate unique tariff keys and build lookup by key.
    by_key: dict[str, UtilityTariffConfig] = {}
    for tcfg in utility_tariffs:
        key = tcfg.tariff_key.strip()
        if not key:
            raise ValueError("Each utility tariff must have a non-empty tariff_key.")
        if key in by_key:
            raise ValueError(f"Duplicate tariff_key in utility_tariffs: {key!r}")
        by_key[key] = tcfg

    overrides = getattr(case_cfg, "node_utility_tariff", None) or {}
    for node in overrides:
        if node not in nodes:
            raise ValueError(f"node_utility_tariff includes unknown node {node!r}")
    for node, key in overrides.items():
        if key not in by_key:
            raise ValueError(
                f"node_utility_tariff[{node!r}] references unknown tariff_key {key!r}"
            )

    node_tariff_key = {n: overrides.get(n, default_key) for n in nodes}
    if any(node_tariff_key[n] not in by_key for n in nodes):
        raise ValueError("Every node must resolve to exactly one known tariff_key.")

    # Cache by full effective spec so repeated tariff definitions only load once.
    cache: dict[tuple[str | None, int | None, str | None, str | None], tuple[Any | None, list[float] | None]] = {}

    def _cache_key(tcfg: UtilityTariffConfig) -> tuple[str | None, int | None, str | None, str | None]:
        urp = str(tcfg.utility_rate_path) if tcfg.utility_rate_path is not None else None
        epp = str(tcfg.energy_price_path) if tcfg.energy_price_path is not None else None
        return (urp, tcfg.utility_rate_item_index, epp, tcfg.energy_price_column)

    tariff_resolved: dict[str, tuple[Any | None, list[float] | None]] = {}
    for key, tcfg in by_key.items():
        ck = _cache_key(tcfg)
        if ck not in cache:
            cache[ck] = _resolve_tariff_source(
                utility_rate_path=tcfg.utility_rate_path,
                utility_rate_item_index=tcfg.utility_rate_item_index,
                energy_price_path=tcfg.energy_price_path,
                energy_price_column=tcfg.energy_price_column,
                timestamps=timestamps,
                n_periods=n_periods,
            )
        tariff_resolved[key] = cache[ck]

    data.import_prices_by_node = {}
    data.utility_rate_by_node = {}
    data.node_utility_tariff_key = dict(node_tariff_key)
    # One shared zero vector for tariffs with no resolved energy prices (memory: O(T), not O(nodes*T)).
    zero_prices = [0.0] * n_periods
    for n in nodes:
        tkey = node_tariff_key[n]
        ur, ip = tariff_resolved[tkey]
        data.utility_rate_by_node[n] = ur
        # Share the same list object for all nodes on the same tariff (no per-node copy).
        data.import_prices_by_node[n] = ip if ip is not None else zero_prices


# ---------------------------------------------------------------------------
# Scenarios: two-stage stochastic input overrides
# ---------------------------------------------------------------------------


_SCENARIO_OVERRIDE_FIELDS = (
    "energy_load",
    "solar_path",
    "wind_path",
    "hydrokinetic_path",
    "utility_tariffs",
)


def _validate_scenarios_config(scenarios: list[ScenarioConfig]) -> None:
    """Fail fast on structurally invalid scenario probabilities/keys.

    Single scenario: probability must be None or 1.0 (not user-settable) -- this rule is
    specific to raw ``ScenarioConfig`` input (only config objects carry an unresolved
    ``None`` probability; by the time a probability reaches ``DataContainer``/``model.core``
    it has already been resolved to a concrete float), so it lives here rather than in the
    shared validator. Everything else (non-empty, no blank keys, no duplicates, every
    probability > 0, probabilities sum to 1.0 within 1e-6) delegates to
    ``shared.scenario_helpers.validate_scenario_probabilities`` -- the single implementation
    also used by ``DataContainer.validate_scenarios`` and ``model.core.build_model``, so the
    three validation layers can't drift the way they previously did.
    """
    if not scenarios:
        raise ValueError("scenarios must be non-empty when CaseConfig.scenarios is set.")

    if len(scenarios) == 1:
        prob = scenarios[0].probability
        if prob is not None and abs(prob - 1.0) > 1e-6:
            raise ValueError(
                "scenarios[0].probability must be None or 1.0 when only one scenario is "
                f"given (probability is not user-settable for a single scenario); got {prob}."
            )
        probability_by_key = {scenarios[0].scenario_key: 1.0}
    else:
        missing = [s.scenario_key for s in scenarios if s.probability is None]
        if missing:
            raise ValueError(
                "scenarios must set probability when more than one scenario is given; "
                f"missing for: {missing}"
            )
        probability_by_key = {s.scenario_key: s.probability for s in scenarios}  # type: ignore[misc]

    validate_scenario_probabilities([s.scenario_key for s in scenarios], probability_by_key)


def _validate_scenario_category_completeness(scenarios: list[ScenarioConfig]) -> None:
    """Each override field must be set on ALL scenarios or NONE — no silent partial fallback.

    A scenario that forgets to set (say) hydrokinetic_path while its siblings do would
    otherwise silently fall back to the base resource for that one scenario, defeating the
    point of declaring scenarios in the first place.

    ``node_utility_tariff`` is deliberately excluded from ``_SCENARIO_OVERRIDE_FIELDS``: it's
    a per-node *modifier* of ``utility_tariffs`` (which tariff a node uses), not an
    independent override category -- forcing it all-or-nothing across scenarios would wrongly
    reject a scenario that's happy with the default tariff assignment. Instead, since
    ``_load_scenarios`` only ever reads a scenario's ``node_utility_tariff`` when that same
    scenario also sets ``utility_tariffs`` (see ``run.build_run_data._load_scenarios``), a
    scenario setting ``node_utility_tariff`` alone would have it silently ignored -- checked
    here instead, fail-fast rather than a no-op.
    """
    for field_name in _SCENARIO_OVERRIDE_FIELDS:
        set_on = [s.scenario_key for s in scenarios if getattr(s, field_name) is not None]
        if set_on and len(set_on) != len(scenarios):
            missing = [s.scenario_key for s in scenarios if getattr(s, field_name) is None]
            raise ValueError(
                f"scenarios: {field_name!r} must be set on all scenarios or none; "
                f"set on {set_on}, missing on {missing}."
            )

    orphaned = [
        s.scenario_key for s in scenarios if s.node_utility_tariff is not None and s.utility_tariffs is None
    ]
    if orphaned:
        raise ValueError(
            "scenarios: 'node_utility_tariff' requires 'utility_tariffs' to also be set on "
            f"the same scenario (it selects among that scenario's tariffs); set without "
            f"utility_tariffs on: {orphaned}."
        )


def _seeded_resource_overlay(data: DataContainer) -> DataContainer:
    """Fresh scratch DataContainer seeded with what resource loaders require.

    ``load_solar_into_container``/``load_wind_into_container``/``load_hydrokinetic_into_container``
    align onto ``timeseries["datetime"]`` and scale by ``static["time_step_hours"]``. Every
    scenario's resource override must align onto the *same* model timesteps as the base
    case (model.T is built once, not per scenario), so both are seeded from the base data
    rather than left to the loader's own defaults (which would silently assume 1-hour steps).
    """
    overlay = DataContainer()
    overlay.timeseries["datetime"] = data.timeseries.get("datetime")
    overlay.static["time_step_hours"] = data.static.get("time_step_hours")
    return overlay


def _load_scenarios(
    data: DataContainer,
    case_cfg: CaseConfig,
    *,
    timestamps: list[Any],
    n_periods: int,
) -> None:
    """Populate ``data.scenario_keys``/``scenario_probability``/``scenario_*`` overrides.

    With no ``CaseConfig.scenarios``, ``data`` keeps its default single-implicit-scenario
    state (``DataContainer`` field defaults), so a plain case behaves exactly like today's
    deterministic model.
    """
    scenarios = getattr(case_cfg, "scenarios", None)
    if scenarios is None:
        return

    _validate_scenarios_config(scenarios)
    _validate_scenario_category_completeness(scenarios)

    data.scenario_keys = [s.scenario_key for s in scenarios]
    data.scenario_probability = (
        {scenarios[0].scenario_key: 1.0}
        if len(scenarios) == 1
        else {s.scenario_key: float(s.probability) for s in scenarios}  # type: ignore[arg-type]
    )

    nodes = list(data.static.get("electricity_load_keys") or [])

    for s in scenarios:
        if s.energy_load is not None:
            overlay = load_energy_load(s.energy_load)
            overlay_nodes = list(overlay.static.get("electricity_load_keys") or [])
            if overlay_nodes != nodes:
                raise ValueError(
                    f"scenario {s.scenario_key!r}: energy_load resolves to node keys "
                    f"{overlay_nodes} but the base case has {nodes}; scenario load files "
                    "must define the same nodes as the base case."
                )
            overlay_n = len(overlay.indices.get("time") or [])
            if overlay_n != n_periods:
                raise ValueError(
                    f"scenario {s.scenario_key!r}: energy_load has {overlay_n} periods but "
                    f"the base case has {n_periods}; scenario load files must match the "
                    "base case's horizon length."
                )
            scenario_ts = data.scenario_timeseries.setdefault(s.scenario_key, {})
            for node in overlay_nodes:
                scenario_ts[node] = overlay.timeseries[node]

        if s.solar_path is not None:
            if not s.solar_path.exists():
                raise FileNotFoundError(
                    f"scenario {s.scenario_key!r}: solar_path set but file missing: {s.solar_path}"
                )
            overlay = _seeded_resource_overlay(data)
            load_solar_into_container(overlay, s.solar_path)
            scenario_ts = data.scenario_timeseries.setdefault(s.scenario_key, {})
            for key in overlay.static.get("solar_production_keys") or []:
                scenario_ts[key] = overlay.timeseries[key]

        if s.wind_path is not None:
            if not s.wind_path.exists():
                raise FileNotFoundError(
                    f"scenario {s.scenario_key!r}: wind_path set but file missing: {s.wind_path}"
                )
            overlay = _seeded_resource_overlay(data)
            load_wind_into_container(overlay, s.wind_path)
            scenario_ts = data.scenario_timeseries.setdefault(s.scenario_key, {})
            for key in overlay.static.get("wind_production_keys") or []:
                scenario_ts[key] = overlay.timeseries[key]

        if s.hydrokinetic_path is not None:
            if not s.hydrokinetic_path.exists():
                raise FileNotFoundError(
                    f"scenario {s.scenario_key!r}: hydrokinetic_path set but file missing: "
                    f"{s.hydrokinetic_path}"
                )
            overlay = _seeded_resource_overlay(data)
            # Sub-fields left unset on the scenario inherit the base case's own configured
            # value (per ScenarioConfig's documented "None means use the case's base value"
            # contract) rather than a hardcoded default -- a scenario that overrides only
            # hydrokinetic_path must not silently get a different reference scaling than the
            # case actually configures.
            resolved_reference_kw = (
                s.hydrokinetic_reference_kw
                if s.hydrokinetic_reference_kw is not None
                else getattr(case_cfg, "hydrokinetic_reference_kw", 1.0)
            )
            resolved_reference_swept_area_m2 = (
                s.hydrokinetic_reference_swept_area_m2
                if s.hydrokinetic_reference_swept_area_m2 is not None
                else getattr(case_cfg, "hydrokinetic_reference_swept_area_m2", None)
            )
            load_hydrokinetic_into_container(
                overlay,
                s.hydrokinetic_path,
                reference_kw=resolved_reference_kw,
                datetime_column=(
                    s.hydrokinetic_datetime_column
                    if s.hydrokinetic_datetime_column is not None
                    else getattr(case_cfg, "hydrokinetic_datetime_column", None)
                ),
                reference_swept_area_m2=resolved_reference_swept_area_m2,
            )
            scenario_ts = data.scenario_timeseries.setdefault(s.scenario_key, {})
            for key in overlay.static.get("hydrokinetic_production_keys") or []:
                scenario_ts[key] = overlay.timeseries[key]
            # The series above was normalized (divided) by resolved_reference_kw at load time;
            # technologies.hydrokinetic.block must de-normalize using this SAME value, not the
            # base case's, so carry it through per scenario (see DataContainer.scenario_hydro-
            # kinetic_reference_kw/_swept_area_m2 for why this can't just fall back to base).
            data.scenario_hydrokinetic_reference_kw[s.scenario_key] = float(resolved_reference_kw)
            if resolved_reference_swept_area_m2 is not None:
                data.scenario_hydrokinetic_reference_swept_area_m2[s.scenario_key] = float(
                    resolved_reference_swept_area_m2
                )

        if s.utility_tariffs is not None:
            # The base case's equivalent tariff entries are guaranteed to have a price source
            # by _validate_utility_mode_config (called earlier in build_run_data), but that
            # function only ever sees case_cfg -- it never runs against a ScenarioConfig, so
            # without this check a scenario tariff entry that sets neither utility_rate_path
            # nor energy_price_path would silently resolve to a $0/kWh import price with no
            # error (found in review).
            for tcfg in s.utility_tariffs:
                if tcfg.utility_rate_path is None and tcfg.energy_price_path is None:
                    raise ValueError(
                        f"scenario {s.scenario_key!r}: utility_tariffs entry {tcfg.tariff_key!r} "
                        "must set utility_rate_path and/or energy_price_path."
                    )
            overlay = DataContainer()
            overlay.static["electricity_load_keys"] = nodes
            # ScenarioConfig has no legacy single-tariff fields, so getattr(s, "utility_rate_path",
            # None) etc. inside _load_utilities_multi naturally resolve to None, satisfying its
            # mutual-exclusivity guard without needing a separate stand-in object.
            _load_utilities_multi(
                overlay, s, s.utility_tariffs, timestamps=timestamps, n_periods=n_periods,
            )
            data.scenario_import_prices_by_node[s.scenario_key] = overlay.import_prices_by_node or {}
            data.scenario_utility_rate_by_node[s.scenario_key] = overlay.utility_rate_by_node or {}


# ---------------------------------------------------------------------------
# Public entry point: orchestrator
# ---------------------------------------------------------------------------


def build_run_data(project_root: Path, case_cfg: CaseConfig) -> DataContainer:
    """Load all case inputs into a single DataContainer.

    - Energy load (required)
    - Solar resource (if case_cfg.solar_path set)
    - Hydrokinetic resource (if case_cfg.hydrokinetic_path set)
    - Utility: node-scoped import prices and optional rate metadata, controlled by
      ``case_cfg.utility_mode`` (priced_grid / free_grid / islanded). Resolves to
      ``data.import_prices_by_node`` and ``data.utility_rate_by_node`` when grid is modeled.
    - Scenarios: optional two-stage stochastic overrides (``case_cfg.scenarios``). With none
      set, ``data`` keeps the default single implicit scenario at probability 1.0.

    Future: export rates, post-processing can be added here without expanding the
    playground script.
    """
    data = load_energy_load(case_cfg.energy_load)
    _load_resources(data, case_cfg)

    _validate_utility_mode_config(case_cfg)

    timestamps = data.timeseries.get("datetime") or []
    n_periods = len(data.indices.get("time") or [])

    # If multi-tariff config is provided, it is authoritative; otherwise use the legacy single-tariff fields.
    utility_tariffs = getattr(case_cfg, "utility_tariffs", None)
    if utility_tariffs is not None:
        _load_utilities_multi(
            data, case_cfg, utility_tariffs,
            timestamps=timestamps, n_periods=n_periods,
        )
    else:
        _load_utilities_single(
            data, case_cfg,
            timestamps=timestamps, n_periods=n_periods,
        )

    _load_scenarios(data, case_cfg, timestamps=timestamps, n_periods=n_periods)

    # Subset last: slice every per-timestep series (timeseries + import_prices + scenario
    # overrides) in one place so lengths stay aligned.
    if case_cfg.time_subset is not None:
        data = apply_time_subset(data, case_cfg.time_subset)

    return data
