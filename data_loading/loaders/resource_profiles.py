"""Loaders for resource profile time series (solar, hydrokinetic, etc.) aligned to a target time vector."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import numpy as np
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


# Align resource timestamps to load when files use different years (e.g. HKT 2020, load 2022).
_CALENDAR_ANCHOR_YEAR = 2004  # leap year so Feb 29 in source maps consistently


def _to_calendar_anchor(dt: datetime) -> datetime:
    """Map wall-clock time into a fixed leap year for joint interpolation with load datetimes."""
    ts = pd.Timestamp(dt)
    try:
        out = ts.replace(year=_CALENDAR_ANCHOR_YEAR)
    except ValueError:
        out = ts.replace(year=_CALENDAR_ANCHOR_YEAR, month=2, day=28)
    return out.to_pydatetime()


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
    # Text / ISO: use format="mixed" to avoid "Could not infer format" warning (pandas 2.0+)
    try:
        return pd.to_datetime(series, format="mixed", errors="coerce")
    except (TypeError, ValueError):
        return pd.to_datetime(series, errors="coerce")


def _time_of_year_minutes(dt: datetime) -> float:
    """Minutes from start of year (0 to 525599 for non-leap)."""
    start = datetime(dt.year, 1, 1, 0, 0, 0)
    delta = dt - start
    return delta.total_seconds() / 60.0


def _coerce_resource_value_columns_to_numeric(
    profile_df: pd.DataFrame, value_col_names: list[str]
) -> None:
    """In-place: coerce value columns so object/str/blanks from CSV/Excel become float NaN."""
    for c in value_col_names:
        profile_df[c] = pd.to_numeric(profile_df[c], errors="coerce")


def _select_numeric_resource_columns(
    profile_df: pd.DataFrame,
    value_col_names: list[str],
    value_columns: list[str] | None,
    *,
    file_path: Path,
    resource_label: str,
) -> list[str]:
    """After coercion, columns with at least one finite value; optional explicit subset."""
    candidates = list(value_col_names)
    if value_columns is not None:
        wanted = {str(c).strip() for c in value_columns}
        available = set(candidates)
        unknown = wanted - available
        if unknown:
            raise ValueError(
                f"{resource_label}: value_columns include unknown headers {sorted(unknown)!r} in {file_path}. "
                f"Columns: {value_col_names}"
            )
        candidates = [c for c in candidates if c in wanted]
    numeric_cols = [c for c in candidates if profile_df[c].notna().any()]
    if not numeric_cols:
        raise ValueError(
            f"No numeric {resource_label} columns in {file_path} after coercion (all blank or non-numeric). "
            f"Found: {value_col_names}"
        )
    return numeric_cols


def _raise_if_aligned_contains_nan(
    aligned: dict[str, list[float]],
    *,
    file_path: Path,
    resource_label: str,
) -> None:
    """Fail fast before Pyomo sees NaN params."""
    bad = [name for name, vals in aligned.items() if any(pd.isna(v) for v in vals)]
    if bad:
        raise ValueError(
            f"{resource_label}: after alignment/interpolation, NaN remains in columns {sorted(bad)!r} "
            f"({file_path}). Source may be all-missing or all-negative for those series."
        )


def _linear_interpolate_series_to_target_minutes(
    series: pd.Series,
    target_minutes: np.ndarray,
    *,
    interpolation_method: str,
    treat_negative_as_missing: bool,
) -> list[float]:
    """1D linear interpolation in time-of-year minutes (smoother than nearest-neighbor)."""
    s = series.groupby(series.index).mean().sort_index()
    if treat_negative_as_missing:
        s = s.where(s >= 0)
    s = s.interpolate(method="index", limit_direction="both")
    s = s.ffill().bfill()
    xp = np.asarray(s.index.values, dtype=float)
    fp = np.asarray(s.values, dtype=float)
    if xp.size == 0 or fp.size == 0:
        return [float("nan")] * len(target_minutes)
    x = np.asarray(target_minutes, dtype=float)
    out = np.interp(x, xp, fp, left=float(fp[0]), right=float(fp[-1]))
    ser = pd.Series(out)
    if treat_negative_as_missing:
        ser = ser.where(ser >= 0)
    ser = ser.interpolate(method=interpolation_method, limit_direction="both")
    filled = ser.ffill().bfill()
    return [float(v) for v in filled.tolist()]


def _align_power_kw_columns_to_datetimes(
    profile_df: pd.DataFrame,
    numeric_cols: list[str],
    target_datetimes: list[datetime],
    *,
    interpolation_method: str,
    treat_negative_as_missing: bool,
) -> dict[str, list[float]]:
    """Interpolate irregular / sub-hour HKT power (kW) onto each load timestamp.

    Uses a calendar anchor year so resource and load files may use different years but the same
    month/day/hour. Duplicate timestamps (after anchoring) are averaged.
    """
    tgt_anchor = pd.DatetimeIndex([_to_calendar_anchor(t) for t in target_datetimes])
    aligned: dict[str, list[float]] = {}
    for col in numeric_cols:
        s = pd.Series(profile_df[col].to_numpy(dtype=float), index=pd.DatetimeIndex(profile_df.index))
        s = s.sort_index()
        s = s.loc[s.index.notna()]
        if s.empty:
            raise ValueError(f"Hydrokinetic column {col!r} has no valid timestamps after parsing")
        idx_anchor = pd.DatetimeIndex([_to_calendar_anchor(t) for t in s.index])
        s = pd.Series(s.values, index=idx_anchor).groupby(level=0).mean().sort_index()
        full = s.index.union(tgt_anchor).sort_values()
        expanded = s.reindex(full)
        expanded = expanded.interpolate(method="time", limit_direction="both")
        expanded = expanded.ffill().bfill()
        out = expanded.reindex(tgt_anchor)
        if treat_negative_as_missing:
            out = out.where(out >= 0)
        out = out.interpolate(method=interpolation_method, limit_direction="both")
        aligned[col] = [float(x) for x in out.tolist()]
    return aligned


def _load_resource_profile_file_into_container(
    data: DataContainer,
    file_path: Path,
    *,
    timeseries_key_prefix: str,
    static_keys_key: str,
    static_units_key: str,
    static_columns_key: str,
    resource_label: str,
    datetime_column: str | None = None,
    value_columns: list[str] | None = None,
    treat_negative_as_missing: bool = True,
    interpolation_method: str = "linear",
    sheet_name: int | str = 0,
) -> None:
    """Load one or more numeric profile columns from CSV or Excel, aligned to load datetimes.

    Value columns are coerced with ``pd.to_numeric(..., errors="coerce")`` so object/str/blanks
    from spreadsheets still load. Alignment uses **linear interpolation** in time-of-year minutes
    (not nearest-neighbor). If any aligned column still contains NaN after cleaning, raises.

    Each column becomes ``{timeseries_key_prefix}{normalized_column_name}`` in ``data.timeseries``.
    Values are treated like capacity factor (0–1) per kW installed, multiplied by
    ``data.static["time_step_hours"]`` to yield kWh/kW per interval (same convention as solar).

    ``static_keys_key`` receives the ordered list of timeseries keys created.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"{resource_label} file not found: {file_path}")

    target_datetimes = data.timeseries.get("datetime") or []
    if not target_datetimes:
        raise ValueError("DataContainer has no time vector (timeseries['datetime'] empty)")

    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=0, engine="openpyxl")
    elif suffix == ".xls":
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=0, engine="xlrd")
    else:
        df = pd.read_csv(file_path)

    df = df.dropna(how="all", axis=0).dropna(how="all", axis=1)
    if df.empty:
        raise ValueError(f"No data rows in {file_path}")

    df.columns = [str(c).strip() for c in df.columns]
    raw_cols = list(df.columns)

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
            if pd.api.types.is_numeric_dtype(first):
                sample = first.dropna()
                if len(sample) > 0:
                    v = sample.iloc[0]
                    if v >= 2e4 or v >= 1e5:
                        time_col = raw_cols[0]
            elif first.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", na=False).any():
                time_col = raw_cols[0]

    if time_col is not None:
        value_col_names = [c for c in raw_cols if c != time_col]
        time_parsed = _parse_time_column(df[time_col], file_path)
        profile_df = df[value_col_names].copy()
        profile_df.index = time_parsed
        profile_df = profile_df.loc[profile_df.index.notna()]
        minutes_of_year = profile_df.index.map(_time_of_year_minutes)
        profile_df = profile_df.set_axis(minutes_of_year)
    else:
        n_rows = len(df)
        interval_min = _infer_interval_minutes_from_row_count(n_rows)
        synthetic_index = _build_synthetic_year_index(n_rows, interval_min)
        value_col_names = raw_cols
        profile_df = df[value_col_names].copy()
        profile_df.index = synthetic_index
        minutes_of_year = profile_df.index.map(_time_of_year_minutes)
        profile_df = profile_df.set_axis(minutes_of_year)

    if not value_col_names:
        raise ValueError(f"No {resource_label} data columns in {file_path}")

    _coerce_resource_value_columns_to_numeric(profile_df, value_col_names)
    numeric_cols = _select_numeric_resource_columns(
        profile_df,
        value_col_names,
        value_columns,
        file_path=file_path,
        resource_label=resource_label,
    )

    target_minutes = np.array(
        [_time_of_year_minutes(dt) for dt in target_datetimes], dtype=float
    )

    aligned: dict[str, list[float]] = {}
    for col in numeric_cols:
        aligned[col] = _linear_interpolate_series_to_target_minutes(
            profile_df[col],
            target_minutes,
            interpolation_method=interpolation_method,
            treat_negative_as_missing=treat_negative_as_missing,
        )
    _raise_if_aligned_contains_nan(aligned, file_path=file_path, resource_label=resource_label)

    dt_hours = data.static.get("time_step_hours")
    if dt_hours is None:
        dt_hours = 1.0
    for col in numeric_cols:
        aligned[col] = [float(v) * dt_hours for v in aligned[col]]

    production_keys: list[str] = []
    for col in numeric_cols:
        suffix_key = _normalize_series_key(str(col))
        key = f"{timeseries_key_prefix}{suffix_key}"
        production_keys.append(key)
        data.timeseries[key] = aligned[col]

    data.static[static_keys_key] = production_keys
    data.static[static_units_key] = "kWh/kW"
    data.static[static_columns_key] = list(numeric_cols)


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
    Value columns are coerced to numeric; alignment along time-of-year uses linear interpolation.
    Negatives are treated as missing; remaining NaNs after interpolation raise a clear error.

    - If the file has a time column: alignment is by time-of-year.
    - If no time column: time step is inferred from row count (8760=hourly, 35040=15-min, etc.).
    """
    _load_resource_profile_file_into_container(
        data,
        solar_path,
        timeseries_key_prefix="solar_production__",
        static_keys_key="solar_production_keys",
        static_units_key="solar_production_units",
        static_columns_key="solar_production_columns",
        resource_label="Solar",
        datetime_column=datetime_column,
        value_columns=solar_columns,
        treat_negative_as_missing=treat_negative_as_missing,
        interpolation_method=interpolation_method,
    )


def load_hydrokinetic_into_container(
    data: DataContainer,
    hydrokinetic_path: Path,
    *,
    datetime_column: str | None = None,
    hydrokinetic_columns: list[str] | None = None,
    treat_negative_as_missing: bool = True,
    interpolation_method: str = "linear",
    sheet_name: int | str = 0,
    reference_kw: float = 1.0,
    reference_swept_area_m2: float | None = None,
) -> None:
    """Load hydrokinetic (HKT) resource profiles from CSV or Excel into the container.

    File values are treated as **average electrical power (kW)** for each timestamp (e.g. hourly
    mean power). Each column is one river location / profile. Value columns are coerced with
    ``pd.to_numeric(..., errors="coerce")``. Series are **aligned to
    ``data.timeseries["datetime"]``** by calendar month/day/hour using time-based interpolation,
    so irregular or non-hourly rows are supported. Resource and load files may use different
    calendar years (alignment uses a fixed leap-year anchor). Any NaN left after interpolation
    raises with the offending column names.

    Stored values are **kWh per kW of installed capacity** for each model timestep, matching
    solar's ``kWh/kW`` convention:

    ``kWh/kW = (power_kW / reference_kw) * time_step_hours``

    Use ``reference_kw=1.0`` (default) when each column is already expressed as kW output **per
    kW installed** at that site. Use a positive rated/reference capacity (kW) when the column is
    **absolute** available kW for a device of that size (divide to normalize per kW).

    Timeseries keys: ``hydrokinetic_production__{normalized_header}``. Metadata:
    ``hydrokinetic_production_keys``, ``hydrokinetic_production_units``, ``hydrokinetic_production_columns``,
    ``hydrokinetic_reference_kw``. If ``reference_swept_area_m2`` is set, also stores
    ``hydrokinetic_reference_swept_area_m2`` on ``data.static`` for the hydrokinetic Pyomo block
    (scales resource to swept area; not hardcoded).
    """
    if reference_kw <= 0:
        raise ValueError(f"reference_kw must be positive; got {reference_kw}")

    file_path = Path(hydrokinetic_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Hydrokinetic file not found: {file_path}")

    target_datetimes = data.timeseries.get("datetime") or []
    if not target_datetimes:
        raise ValueError("DataContainer has no time vector (timeseries['datetime'] empty)")

    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=0, engine="openpyxl")
    elif suffix == ".xls":
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=0, engine="xlrd")
    else:
        df = pd.read_csv(file_path)

    df = df.dropna(how="all", axis=0).dropna(how="all", axis=1)
    if df.empty:
        raise ValueError(f"No data rows in {file_path}")

    df.columns = [str(c).strip() for c in df.columns]
    raw_cols = list(df.columns)

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
            if pd.api.types.is_numeric_dtype(first):
                sample = first.dropna()
                if len(sample) > 0:
                    v = sample.iloc[0]
                    if v >= 2e4 or v >= 1e5:
                        time_col = raw_cols[0]
            elif first.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", na=False).any():
                time_col = raw_cols[0]

    if time_col is not None:
        value_col_names = [c for c in raw_cols if c != time_col]
        time_parsed = _parse_time_column(df[time_col], file_path)
        profile_df = df[value_col_names].copy()
        profile_df.index = time_parsed
        profile_df = profile_df.loc[profile_df.index.notna()]
    else:
        n_rows = len(df)
        interval_min = _infer_interval_minutes_from_row_count(n_rows)
        synthetic_index = _build_synthetic_year_index(n_rows, interval_min)
        value_col_names = raw_cols
        profile_df = df[value_col_names].copy()
        profile_df.index = synthetic_index

    if not value_col_names:
        raise ValueError(f"No Hydrokinetic data columns in {file_path}")

    _coerce_resource_value_columns_to_numeric(profile_df, value_col_names)
    numeric_cols = _select_numeric_resource_columns(
        profile_df,
        value_col_names,
        hydrokinetic_columns,
        file_path=file_path,
        resource_label="Hydrokinetic",
    )

    aligned_kw = _align_power_kw_columns_to_datetimes(
        profile_df,
        numeric_cols,
        target_datetimes,
        interpolation_method=interpolation_method,
        treat_negative_as_missing=treat_negative_as_missing,
    )
    _raise_if_aligned_contains_nan(
        aligned_kw, file_path=file_path, resource_label="Hydrokinetic"
    )

    dt_hours = data.static.get("time_step_hours")
    if dt_hours is None:
        dt_hours = 1.0

    production_keys: list[str] = []
    for col in numeric_cols:
        suffix_key = _normalize_series_key(str(col))
        key = f"hydrokinetic_production__{suffix_key}"
        production_keys.append(key)
        data.timeseries[key] = [
            (p_kw / reference_kw) * dt_hours for p_kw in aligned_kw[col]
        ]

    data.static["hydrokinetic_production_keys"] = production_keys
    data.static["hydrokinetic_production_units"] = "kWh/kW"
    data.static["hydrokinetic_production_columns"] = list(numeric_cols)
    data.static["hydrokinetic_reference_kw"] = reference_kw
    if reference_swept_area_m2 is not None:
        if reference_swept_area_m2 <= 0:
            raise ValueError(f"reference_swept_area_m2 must be positive; got {reference_swept_area_m2}")
        data.static["hydrokinetic_reference_swept_area_m2"] = float(reference_swept_area_m2)
