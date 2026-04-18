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
)
from data_loading.loaders.utility_rates import (
    get_import_prices_for_timestamps,
    load_raw_energy_prices,
)
from data_loading.schemas import DataContainer
from data_loading.time_subset import apply_time_subset

if TYPE_CHECKING:
    from config.case_config import CaseConfig, UtilityTariffConfig


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
    utility_rate, import_prices = _resolve_tariff_source(
        utility_rate_path=getattr(case_cfg, "utility_rate_path", None),
        utility_rate_item_index=getattr(case_cfg, "utility_rate_item_index", None),
        energy_price_path=getattr(case_cfg, "energy_price_path", None),
        energy_price_column=getattr(case_cfg, "energy_price_column", None),
        timestamps=timestamps,
        n_periods=n_periods,
    )

    nodes = list(data.static.get("electricity_load_keys") or [])
    if not nodes:
        raise ValueError(
            "single-tariff utility setup requires non-empty data.static['electricity_load_keys']."
        )

    # Share one zero vector across nodes when no prices resolved (memory: O(T), not O(nodes*T)).
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
# Public entry point: orchestrator
# ---------------------------------------------------------------------------


def build_run_data(project_root: Path, case_cfg: CaseConfig) -> DataContainer:
    """Load all case inputs into a single DataContainer.

    - Energy load (required)
    - Solar resource (if case_cfg.solar_path set)
    - Hydrokinetic resource (if case_cfg.hydrokinetic_path set)
    - Utility: node-scoped import prices and optional rate metadata (if energy_price_path
      or utility_rate_path set, or multi-tariff ``utility_tariffs`` provided). Resolves to
      ``data.import_prices_by_node`` and ``data.utility_rate_by_node``.

    Future: wind, export rates, time subset, post-processing can be added here
    without expanding the playground script.
    """
    data = load_energy_load(case_cfg.energy_load)
    _load_resources(data, case_cfg)

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

    # Subset last: slice every per-timestep series (timeseries + import_prices) in one place so lengths stay aligned.
    if case_cfg.time_subset is not None:
        data = apply_time_subset(data, case_cfg.time_subset)

    return data
