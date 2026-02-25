"""Loaders for resource profile time series (solar, wind, hydro) aligned to a target time vector."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_loading.schemas import DataContainer

# Row counts for one year → interval minutes (no time column)
_ROW_COUNT_TO_INTERVAL_MINUTES = {
    8760: 60,      # hourly
    17520: 30,     # 30-min
    35040: 15,     # 15-min
    105120: 5,     # 5-min
}

# Default time column names to look for
_TIME_COLUMN_CANDIDATES = ("Date", "Time", "datetime", "Timestamp", "date", "time")

# Pattern for solar-related column names (kW, kWh, capacity factor, etc.)
_SOLAR_UNIT_PATTERN = re.compile(
    r"\b(kw|kwh|kw/kw|kwh/kw|capacity\s*factor|cf)\b", re.IGNORECASE
)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _normalize_series_key(name: str) -> str:
    """Stable key suffix from column name."""
    return _NON_ALPHANUMERIC.sub("_", name.lower()).strip("_")


def _infer_interval_minutes_from_row_count(n: int) -> int:
    """Infer time step in minutes from number of rows (one year of data)."""
    if n in _ROW_COUNT_TO_INTERVAL_MINUTES:
        return _ROW_COUNT_TO_INTERVAL_MINUTES[n]
    # 60 * 8760 / n = minutes per period for one year
    if n <= 0 or n > 525600:
        raise ValueError(
            f"Cannot infer time step from row count {n} (expected e.g. 8760, 35040, 105120)"
        )
    interval_min = int(round(60 * 8760 / n))
    if interval_min < 1:
        interval_min = 1
    return interval_min


def _build_synthetic_year_index(n_rows: int, interval_minutes: int) -> pd.DatetimeIndex:
    """Build a datetime index for one year at the given interval (no time column case)."""
    # Use a non-leap year for consistent length
    start = datetime(2021, 1, 1, 0, 0, 0)
    freq = f"{interval_minutes}min" if interval_minutes < 60 else "1h"
    if interval_minutes == 60:
        freq = "1h"
    idx = pd.date_range(start=start, periods=n_rows, freq=freq)
    return idx


def _parse_time_column(series: pd.Series, file_path: Path) -> pd.DatetimeIndex:
    """Parse a time column (numeric serial or text) into DatetimeIndex."""
    # Try numeric (Excel or MATLAB serial)
    try:
        first = series.dropna().iloc[0]
        if pd.api.types.is_number(first):
            serial = pd.to_numeric(series, errors="coerce")
            # Excel range ~2e4-5e4, MATLAB ~7e5+
            if first < 100_000:
                # Excel serial
                from data_loading.loaders.energy_load import _excel_serial_to_datetime
                dts = serial.map(lambda x: _excel_serial_to_datetime(x) if pd.notna(x) else pd.NaT)
            else:
                from data_loading.loaders.energy_load import _matlab_serial_to_datetime
                dts = serial.map(lambda x: _matlab_serial_to_datetime(x) if pd.notna(x) else pd.NaT)
            return pd.DatetimeIndex(dts)
    except (ValueError, TypeError, IndexError):
        pass
    # Text / ISO
    return pd.to_datetime(series, errors="coerce")


def _time_of_year_minutes(dt: datetime) -> float:
    """Minutes from start of year (0 to 525599 for non-leap)."""
    start = datetime(dt.year, 1, 1, 0, 0, 0)
    delta = dt - start
    return delta.total_seconds() / 60.0


def load_solar_into_container(
    data: DataContainer,
    solar_path: Path,
    *,
    datetime_column: str | None = None,
    solar_columns: list[str] | None = None,
    treat_negative_as_missing: bool = True,
    interpolation_method: str = "linear",
) -> None:
    """Load solar resource profile from CSV and add to container, aligned to load time vector.

    Stored as solar_production__{suffix} in kWh per kW capacity (kWh/kW). File is assumed to
    be capacity factor (0–1); values are multiplied by the load time step (data.static["time_step_hours"])
    so output = CF × dt_hours = kWh/kW for that interval.

    Solar data is aligned to the same time series as the load (data.timeseries["datetime"]).
    Negatives are treated as missing; NaNs are filled by interpolation.

    - If the file has a time column: alignment is by time-of-year.
    - If no time column: time step is inferred from row count (8760=hourly, 35040=15-min, etc.).
    """
    solar_path = Path(solar_path)
    if not solar_path.exists():
        raise FileNotFoundError(f"Solar file not found: {solar_path}")

    # Target time vector = load time series (must match load data exactly)
    target_datetimes = data.timeseries["datetime"]
    if not target_datetimes:
        raise ValueError("DataContainer has no time vector (timeseries['datetime'] empty)")

    df = pd.read_csv(solar_path)
    df = df.dropna(how="all", axis=0).dropna(how="all", axis=1)
    if df.empty:
        raise ValueError(f"No data rows in {solar_path}")
    raw_cols = list(df.columns)

    # Detect time column
    time_col: str | None = None
    if datetime_column and datetime_column in raw_cols:
        time_col = datetime_column
    else:
        for cand in _TIME_COLUMN_CANDIDATES:
            if cand in raw_cols:
                time_col = cand
                break
        if time_col is None and len(raw_cols) > 0:
            first = df.iloc[:, 0]
            # First column might be time if numeric and in serial date range (Excel 2e4-5e4, MATLAB 7e5+)
            if pd.api.types.is_numeric_dtype(first):
                sample = first.dropna()
                if len(sample) > 0:
                    v = sample.iloc[0]
                    if v >= 2e4 or v >= 1e5:  # Excel or MATLAB serial range
                        time_col = raw_cols[0]
            elif first.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", na=False).any():
                time_col = raw_cols[0]

    if time_col is not None:
        solar_col_names = [c for c in raw_cols if c != time_col]
        time_parsed = _parse_time_column(df[time_col], solar_path)
        solar_df = df[solar_col_names].copy()
        solar_df.index = time_parsed
        solar_df = solar_df.loc[solar_df.index.notna()]
        minutes_of_year = solar_df.index.map(_time_of_year_minutes)
        solar_df = solar_df.set_axis(minutes_of_year)
    else:
        # No time column: infer interval from row count
        n_rows = len(df)
        interval_min = _infer_interval_minutes_from_row_count(n_rows)
        synthetic_index = _build_synthetic_year_index(n_rows, interval_min)
        solar_col_names = raw_cols
        solar_df = df[solar_col_names].copy()
        solar_df.index = synthetic_index
        minutes_of_year = solar_df.index.map(_time_of_year_minutes)
        solar_df = solar_df.set_axis(minutes_of_year)

    if not solar_col_names:
        raise ValueError(f"No solar data columns in {solar_path}")

    # Filter to numeric columns only
    numeric_cols = [c for c in solar_col_names if pd.api.types.is_numeric_dtype(solar_df[c])]
    if not numeric_cols:
        raise ValueError(
            f"No numeric solar columns in {solar_path}. Found: {solar_col_names}"
        )

    # Align to load time vector by time-of-year (minutes from start of year)
    target_minutes = pd.Series([_time_of_year_minutes(dt) for dt in target_datetimes])
    max_solar = float(solar_df.index.max())
    target_minutes = target_minutes.clip(upper=max_solar)

    aligned = {}
    for col in numeric_cols:
        reindexed = solar_df[col].reindex(target_minutes, method="nearest")
        aligned[col] = reindexed.values.tolist()

    # Filter: no negatives (treat as missing), fill NaNs via interpolation
    for col in numeric_cols:
        series = pd.Series(aligned[col])
        if treat_negative_as_missing:
            series = series.where(series >= 0)
        series = series.interpolate(method=interpolation_method, limit_direction="both")
        aligned[col] = series.tolist()

    # Convert capacity factor (0–1) to kWh per kW capacity: value * dt_hours
    dt_hours = data.static.get("time_step_hours")
    if dt_hours is None:
        dt_hours = 1.0
    for col in numeric_cols:
        aligned[col] = [v * dt_hours for v in aligned[col]]

    # Write into container: solar_production__{suffix}, units kWh/kW
    production_keys: list[str] = []
    for col in numeric_cols:
        suffix = _normalize_series_key(col)
        key = f"solar_production__{suffix}"
        production_keys.append(key)
        data.timeseries[key] = aligned[col]
    data.static["solar_production_keys"] = production_keys
    data.static["solar_production_units"] = "kWh/kW"
    data.static["solar_production_columns"] = numeric_cols
