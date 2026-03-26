"""Build the unified run data container from case config.

Loads energy load, solar, utility (OpenEI or raw 8760/N), and populates a single
DataContainer. Add wind, hydro, export rates, post-processing here so playground
stays a thin entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from data_loading.loaders import load_energy_load, load_openei_rate, load_solar_into_container
from data_loading.loaders.utility_rates import (
    get_import_prices_for_timestamps,
    load_raw_energy_prices,
)
from data_loading.schemas import DataContainer
from data_loading.time_subset import apply_time_subset

if TYPE_CHECKING:
    from config.case_config import CaseConfig


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

    # Utility data can come from two sources:
    # - utility_rate_path (OpenEI/URDB): demand charges + fixed charges + (optionally) energy schedule
    # - energy_price_path (raw vector): overrides energy import prices ($/kWh) per period
    #
    # When both are provided, we use:
    # - import_prices from the raw vector
    # - demand charges / fixed charges from the OpenEI tariff (utility_rate)
    utility_rate = None
    if case_cfg.utility_rate_path is not None:
        if not case_cfg.utility_rate_path.exists():
            raise FileNotFoundError(f"utility_rate_path set but file missing: {case_cfg.utility_rate_path}")
        utility_rate = load_openei_rate(
            case_cfg.utility_rate_path,
            item_index=case_cfg.utility_rate_item_index,
        )
        data.utility_rate = utility_rate

    raw_prices = None
    if case_cfg.energy_price_path is not None:
        if not case_cfg.energy_price_path.exists():
            raise FileNotFoundError(f"energy_price_path set but file missing: {case_cfg.energy_price_path}")
        raw_prices = load_raw_energy_prices(
            case_cfg.energy_price_path,
            price_column=case_cfg.energy_price_column,
        )

    timestamps = data.timeseries.get("datetime") or []
    n_periods = len(data.indices.get("time") or [])
    has_demand_charges = bool(
        utility_rate is not None
        and getattr(utility_rate, "demand_charges", None)
        and getattr(utility_rate, "demand_charges", {}).get("demand_charge_type")
    )

    def _align_raw_prices_to_periods(prices: list[float], n: int) -> list[float]:
        if len(prices) == n:
            return list(prices)
        if len(prices) > n:
            return list(prices[:n])
        raise ValueError(
            f"Raw price series has {len(prices)} values but run has {n} periods; align length or use longer series"
        )

    # Determine the single import_prices vector for the model.
    # Demand charges require calendar mapping, so fail early if tariff has demand charges but no timestamps.
    if has_demand_charges and not timestamps:
        raise ValueError(
            "utility_rate_path includes demand charges but load data has no timeseries['datetime']. "
            "Demand-charge billing windows are month/weekday/weekend/hour based and require timestamps."
        )

    if raw_prices is not None:
        # Raw prices override OpenEI energy rates.
        if timestamps:
            data.import_prices = get_import_prices_for_timestamps(raw_prices, timestamps)
        else:
            data.import_prices = _align_raw_prices_to_periods(raw_prices.prices, n_periods)
    elif utility_rate is not None:
        # OpenEI TOU energy prices require calendar timestamps to map weekday/weekend and month/hour schedules.
        if getattr(utility_rate, "rate_type", None) == "tou":
            if not timestamps:
                raise ValueError(
                    "utility_rate_path provided a TOU tariff but load data has no timeseries['datetime']. "
                    "TOU energy prices require timestamps to map the schedule. "
                    "Fix the load file / loader so datetimes are present, or set energy_price_path to provide an explicit per-period import price vector."
                )
            data.import_prices = get_import_prices_for_timestamps(utility_rate, timestamps)

    # Subset last: slice every per-timestep series (timeseries + import_prices) in one place so lengths stay aligned.
    if case_cfg.time_subset is not None:
        data = apply_time_subset(data, case_cfg.time_subset)

    return data
