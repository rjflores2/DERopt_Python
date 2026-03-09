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

    # One energy price source (raw CSV or OpenEI) so we always produce a single import_prices vector for the model.
    utility_rate = None
    energy_price_source = None
    if case_cfg.energy_price_path is not None:
        if not case_cfg.energy_price_path.exists():
            raise FileNotFoundError(f"energy_price_path set but file missing: {case_cfg.energy_price_path}")
        energy_price_source = load_raw_energy_prices(
            case_cfg.energy_price_path,
            price_column=case_cfg.energy_price_column,
        )
    elif case_cfg.utility_rate_path is not None:
        if not case_cfg.utility_rate_path.exists():
            raise FileNotFoundError(f"utility_rate_path set but file missing: {case_cfg.utility_rate_path}")
        utility_rate = load_openei_rate(
            case_cfg.utility_rate_path,
            item_index=case_cfg.utility_rate_item_index,
        )
        energy_price_source = utility_rate

    timestamps = data.timeseries.get("datetime") or []
    if energy_price_source is not None and timestamps:
        data.import_prices = get_import_prices_for_timestamps(energy_price_source, timestamps)
        data.utility_rate = utility_rate

    # Subset last: slice every per-timestep series (timeseries + import_prices) in one place so lengths stay aligned.
    if case_cfg.time_subset is not None:
        data = apply_time_subset(data, case_cfg.time_subset)

    return data
