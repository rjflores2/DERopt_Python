"""Build the unified run data container from case config.

Loads energy load, solar, utility (OpenEI or raw 8760/N), and populates a single
DataContainer. Add wind, hydro, export rates, post-processing here so playground
stays a thin entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from data_loading.loaders import load_energy_load, load_openei_rate, load_solar_into_container
from data_loading.loaders.utility_rates import (
    get_import_prices_for_timestamps,
    load_raw_energy_prices,
)
from data_loading.schemas import DataContainer
from data_loading.time_subset import apply_time_subset

if TYPE_CHECKING:
    from config.case_config import CaseConfig, UtilityTariffConfig


def build_run_data(project_root: Path, case_cfg: CaseConfig) -> DataContainer:
    """Load all case inputs into a single DataContainer.

    - Energy load (required)
    - Solar resource (if case_cfg.solar_path set)
    - Utility: import price vector and optional rate metadata (if energy_price_path
      or utility_rate_path set). Resolves to data.import_prices and data.utility_rate.

    Future: wind, hydro, export rates, time subset, post-processing can be added here
    without expanding the playground script.
    """
    data = load_energy_load(case_cfg.energy_load)

    if case_cfg.solar_path is not None:
        if not case_cfg.solar_path.exists():
            raise FileNotFoundError(f"solar_path set but file missing: {case_cfg.solar_path}")
        load_solar_into_container(data, case_cfg.solar_path)

    timestamps = data.timeseries.get("datetime") or []
    n_periods = len(data.indices.get("time") or [])

    def _align_raw_prices_to_periods(prices: list[float], n: int) -> list[float]:
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
    ) -> tuple[Any | None, list[float] | None]:
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

    # If multi-tariff config is provided, it is authoritative.
    utility_tariffs = getattr(case_cfg, "utility_tariffs", None)
    if utility_tariffs is not None:
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

        tariffs = utility_tariffs
        default_key = tariffs[0].tariff_key
        if not default_key:
            raise ValueError("utility_tariffs[0].tariff_key must be non-empty (default tariff).")

        # Validate unique tariff keys and build lookup.
        by_key: dict[str, Any] = {}
        for t in tariffs:
            k = t.tariff_key.strip()
            if not k:
                raise ValueError("Each utility tariff must have a non-empty tariff_key.")
            if k in by_key:
                raise ValueError(f"Duplicate tariff_key in utility_tariffs: {k!r}")
            by_key[k] = t

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
                )
            tariff_resolved[key] = cache[ck]

        data.import_prices_by_node = {}
        data.utility_rate_by_node = {}
        data.node_utility_tariff_key = dict(node_tariff_key)
        for n in nodes:
            tkey = node_tariff_key[n]
            ur, ip = tariff_resolved[tkey]
            data.utility_rate_by_node[n] = ur
            data.import_prices_by_node[n] = list(ip) if ip is not None else [0.0] * n_periods

        # Backward-compatible mirrors: set to default tariff.
        d_ur, d_ip = tariff_resolved[default_key]
        data.utility_rate = d_ur
        data.import_prices = list(d_ip) if d_ip is not None else None
    else:
        utility_rate, import_prices = _resolve_tariff_source(
            utility_rate_path=getattr(case_cfg, "utility_rate_path", None),
            utility_rate_item_index=getattr(case_cfg, "utility_rate_item_index", None),
            energy_price_path=getattr(case_cfg, "energy_price_path", None),
            energy_price_column=getattr(case_cfg, "energy_price_column", None),
        )
        data.utility_rate = utility_rate
        data.import_prices = import_prices

    # Subset last: slice every per-timestep series (timeseries + import_prices) in one place so lengths stay aligned.
    if case_cfg.time_subset is not None:
        data = apply_time_subset(data, case_cfg.time_subset)

    return data
