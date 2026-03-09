"""Loader for raw 8760 (or N-length) energy price time series.

Use for wholesale, real-time, or other sources that provide a direct price-per-period
vector instead of OpenEI-style tariff structures. The model utility block consumes
a single import-price vector regardless of source (OpenEI or raw).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class RawEnergyPriceSeries:
    """Energy import prices as a single time-series vector (e.g. 8760 hourly $/kWh).

    Use when the source is wholesale, real-time, or any file/API that provides
    prices per period rather than an OpenEI tariff. The model utility block will
    take this vector (aligned to run timestamps) the same way it takes the
    vector produced from OpenEI via import_prices_for_timestamps().
    """

    prices: list[float]
    """Import price ($/kWh) per period, length 8760 for hourly or N for other resolution."""
    source_label: str = "raw"
    """Short label for the source (e.g. 'wholesale', 'rtp')."""


def load_raw_energy_prices(
    path: Path | str,
    *,
    price_column: str | None = None,
    datetime_column: str | None = None,
    source_label: str = "raw",
) -> RawEnergyPriceSeries:
    """Load a raw 8760 (or N-length) energy price series from CSV.

    CSV with headers: use price_column for $/kWh column, or None to use first numeric column.
    CSV without headers: single column of numeric prices (e.g. 8760 rows).
    datetime_column is reserved for future time-based alignment; for now alignment is by index in get_import_prices_for_timestamps.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Energy price file not found: {path}")

    # Try header first; if no header, first row might be numeric
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Energy price file is empty: {path}")

    if price_column is not None:
        if price_column not in df.columns:
            raise ValueError(
                f"price_column {price_column!r} not in CSV columns: {list(df.columns)}"
            )
        series = pd.to_numeric(df[price_column], errors="coerce")
    else:
        # First numeric column, or only column
        numeric = df.select_dtypes(include=["number"])
        if numeric.empty:
            raise ValueError(
                f"No numeric column found in {path}. Set price_column to the $/kWh column name."
            )
        series = numeric.iloc[:, 0]
    series = series.dropna()
    prices = series.tolist()
    if not prices:
        raise ValueError(f"No valid numeric prices in {path}")
    return RawEnergyPriceSeries(prices=prices, source_label=source_label)
