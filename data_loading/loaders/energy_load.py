"""Loader for generic electricity load timeseries (CSV, xlsx, xls). Units in model: kWh."""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config.case_config import EnergyLoadFileConfig
from data_loading.schemas import DataContainer

_EXCEL_EXTENSIONS = {".xlsx", ".xls"}

# Serial date magnitude thresholds for auto-detect (Excel ~20k–50k, MATLAB ~7e5+).
_SERIAL_EXCEL_MAX = 100_000.0
_SERIAL_MATLAB_MIN = 100_000.0


def _datetime_to_matlab_serial(dt: datetime) -> float:
    """Convert Python datetime to MATLAB-style serial day number."""
    midnight = datetime(dt.year, dt.month, dt.day)
    seconds = (dt - midnight).total_seconds()
    # +366 to handle year 0: Python ordinal starts Jan 1, 0001; MATLAB starts Jan 1, 0000
    return float(dt.toordinal() + 366) + (seconds / 86400.0)


def _matlab_serial_to_datetime(serial: float) -> datetime:
    """Convert MATLAB-style serial day number to Python datetime."""
    day = int(serial)
    frac = serial - day
    # MATLAB epoch: 1 = Jan 1, 0000 → ordinal 1 in Python = Jan 1, 0001 → MATLAB 367.
    ordinal = day - 366
    base = datetime.fromordinal(max(ordinal, 1))
    base = base.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=round(frac * 86400.0))


def _excel_serial_to_datetime(serial: float) -> datetime:
    """Convert Excel-style serial day number to Python datetime (1900 date system).

    Excel incorrectly treats 1900 as a leap year (serial 60 = Feb 29, 1900). For
    serial >= 61 we subtract one day so Mar 1 1900 onward match Excel.
    """
    epoch = datetime(1899, 12, 31, 0, 0, 0, 0)
    if serial >= 61:
        serial = serial - 1  # Excel 1900 leap-year bug: no real Feb 29 1900
    return epoch + timedelta(days=serial)


def _parse_datetime_cell( 
    raw: str | float | datetime, fmt: str, row_idx: int, file_path: Path
) -> datetime:
    """Parse one datetime cell: text (strftime), matlab_serial, excel_serial, or auto."""
    if raw is None or pd.isna(raw): # If there is no data or data is NaN, raise an error
        raise ValueError(f"Row {row_idx}: empty datetime in {file_path}")
    if isinstance(raw, datetime): # If the data is already a datetime, return it
        return raw
    raw = str(raw).strip() # If the data is a string, strip it
    if not raw: # If the data is empty, raise an error
        raise ValueError(f"Row {row_idx}: empty datetime in {file_path}")

    if fmt in ("matlab_serial", "excel_serial", "auto"): # If the format is matlab_serial, excel_serial, or auto, parse the data
        try:# If the data is a float, parse it
            serial = float(raw)
        except ValueError: 
            # auto + text: try common strftime formats
            if fmt == "auto": # If the format is auto, try common strftime formats
                for trial in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        return datetime.strptime(raw, trial)
                    except ValueError:
                        continue
                raise ValueError(
                    f"Row {row_idx}: auto format could not parse text date '{raw}'. "
                    "Specify datetime_format (e.g. '%m/%d/%Y %H:%M') for text dates."
                ) from None
            raise ValueError(
                f"Row {row_idx}: datetime format is '{fmt}' but value is not numeric: '{raw}'"
            ) from None
        if fmt == "matlab_serial": # If the format is matlab_serial, parse the data
            return _matlab_serial_to_datetime(serial)
        if fmt == "excel_serial": # If the format is excel_serial, parse the data
            return _excel_serial_to_datetime(serial)
        # auto: infer by magnitude
        if serial >= _SERIAL_MATLAB_MIN: # If serial number is higher than the typical excel serial number, assume its a matlab serial number
            return _matlab_serial_to_datetime(serial)
        if serial <= _SERIAL_EXCEL_MAX: # Otherwise, assume its an excel serial number
            return _excel_serial_to_datetime(serial)
        # ambiguous middle range: assume Excel (prefer future Excel workflow)
        return _excel_serial_to_datetime(serial)    

    try: # If the format is not matlab_serial, excel_serial, or auto, parse the data as a datetime
        return datetime.strptime(raw, fmt)
    except ValueError as exc:
        raise ValueError(
            f"Row {row_idx}: failed datetime parse '{raw}' with format '{fmt}'"
        ) from exc


def _load_rows_from_excel(
    file_path: Path, cfg: EnergyLoadFileConfig
) -> tuple[list[str], list[dict[str, str | float | datetime | None]]]:
    """Read Excel file and return (fieldnames, list of row dicts)."""
    engine = "openpyxl" if file_path.suffix.lower() == ".xlsx" else "xlrd"
    df = pd.read_excel(
        file_path, sheet_name=cfg.sheet_name, header=0, engine=engine
    )
    df = df.dropna(how="all", axis=0).dropna(how="all", axis=1)
    if df.empty:
        raise ValueError(f"No data rows in Excel sheet: {file_path}")
    raw_cols = [str(c).strip() for c in df.columns]
    fieldnames = _deduplicate_headers(raw_cols)
    rows: list[dict[str, str | float | datetime | None]] = []
    for _, r in df.iterrows():
        rows.append({fieldnames[i]: r.iloc[i] for i in range(len(fieldnames))})
    return fieldnames, rows


def _load_rows_from_csv(
    file_path: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """Read CSV file and return (fieldnames, list of row dicts)."""
    with file_path.open("r", encoding="utf-8-sig", newline="") as f:
        header_reader = csv.reader(f)
        raw_fieldnames = next(header_reader, None)
        if raw_fieldnames is None:
            raise ValueError(f"No header found in CSV: {file_path}")
        fieldnames = _deduplicate_headers(raw_fieldnames)
        reader = csv.DictReader(f, fieldnames=fieldnames)
        rows = list(reader)
    return fieldnames, rows


def _cell_to_str_or_float(val: str | float | datetime | None) -> str | float:
    """Coerce cell value for datetime/float parsing."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, datetime):
        return val  # type: ignore
    return str(val).strip() if isinstance(val, str) else val


# Regex patterns for detecting units in column names
_UNIT_IN_PARENTHESES_PATTERN = re.compile(r"\([^)]*\bkw(?:h)?\b[^)]*\)", re.IGNORECASE)
_KW_WORD_PATTERN = re.compile(r"\bkw\b", re.IGNORECASE)
_KWH_WORD_PATTERN = re.compile(r"\bkwh\b", re.IGNORECASE)
_NON_ALPHANUMERIC_PATTERN = re.compile(r"[^a-z0-9]+")


def _normalize_series_key(header_name: str) -> str:
    """Convert header text to a stable key suffix; use 'load' not 'demand' (kWh = load)."""
    raw = _NON_ALPHANUMERIC_PATTERN.sub("_", header_name.lower()).strip("_")
    return raw.replace("demand", "load")


def _deduplicate_headers(fieldnames: list[str]) -> list[str]:
    """Make duplicate CSV headers unique while preserving order."""
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for name in fieldnames:
        count = seen.get(name, 0) + 1
        seen[name] = count
        deduped.append(name if count == 1 else f"{name} [{count}]")
    return deduped


def _resolve_load_columns(
    fieldnames: list[str], configured_column: str, csv_path: Path
) -> list[str]:
    """Resolve load columns by explicit config or (kW)/(kWh) in header. Duplicate headers are deduplicated earlier (e.g. 'Electric Demand (kW) [2]'), so all matching columns are included."""
    matched_unit_columns = [name for name in fieldnames if _UNIT_IN_PARENTHESES_PATTERN.search(name)]
    selected: list[str] = []

    if configured_column in fieldnames:
        selected.append(configured_column)
        selected.extend([c for c in matched_unit_columns if c != configured_column])
        return selected

    if matched_unit_columns:
        return matched_unit_columns

    raise ValueError(
        f"Missing required load column '{configured_column}' in {csv_path}. "
        f"No '(kW)' or '(kWh)' column was detected. Found columns: {fieldnames}"
    )


def _infer_units_from_header(header_name: str) -> str:
    """Infer load units from selected header name."""
    if _KWH_WORD_PATTERN.search(header_name):
        return "kWh"
    if _KW_WORD_PATTERN.search(header_name):
        return "kW"
    return "unknown"


def _timestamps_are_regular_enough(
    datetimes: list[datetime],
    interval_minutes: int,
    tolerance_seconds: float,
) -> bool:
    """Return True if timestamps are within tolerance of a regular grid at interval_minutes."""
    if len(datetimes) <= 1:
        return True
    idx = pd.DatetimeIndex(datetimes)
    interval_sec = float(interval_minutes * 60)
    start = idx[0]
    deltas_sec = np.asarray((idx - start).total_seconds(), dtype=float)
    remainders = deltas_sec % interval_sec
    dist_to_grid = np.minimum(remainders, interval_sec - remainders)
    max_deviation = float(np.max(dist_to_grid))
    return max_deviation <= tolerance_seconds


def _condition_time_series(
    datetimes: list[datetime],
    series: dict[str, list[float]],
    cfg: EnergyLoadFileConfig,
) -> tuple[list[datetime], dict[str, list[float]]]:
    """Condition time series values (fill NaN/negative) and optionally resample.

    - Always treat negative as missing when configured.
    - Always fill NaN/invalid values via interpolation.
    - Only change the time grid (resample) when target_interval_minutes is set.
      When set, resample to that grid only if timestamps differ significantly from
      the target grid (beyond resample_tolerance_seconds), unless
      resample_only_if_irregular is False.
    """
    if not datetimes:
        return datetimes, series

    df = pd.DataFrame(series, index=pd.DatetimeIndex(datetimes))
    if cfg.treat_negative_as_missing:
        df = df.where(df >= 0)

    interval_min = cfg.target_interval_minutes
    if interval_min is not None:
        freq = f"{interval_min}min" if interval_min < 60 else "1h"
        if interval_min == 60:
            freq = "1h"

        do_resample = True
        if cfg.resample_only_if_irregular:
            if _timestamps_are_regular_enough(
                datetimes,
                interval_min,
                cfg.resample_tolerance_seconds,
            ):
                do_resample = False

        if do_resample:
            df = df.resample(freq).mean()

    # Fill NaN (from gaps, negatives, or resampling) using configured interpolation.
    df = df.interpolate(method=cfg.interpolation_method, limit_direction="both")

    new_dtimes = [t.to_pydatetime() for t in df.index]
    new_series = {col: df[col].tolist() for col in df.columns}
    return new_dtimes, new_series


def load_energy_load(cfg: EnergyLoadFileConfig) -> DataContainer:
    """Load electricity load (kWh) from a CSV or Excel file (.csv, .xlsx, .xls) into a DataContainer.

    Expected columns:
    - cfg.datetime_column (default: Date)
    - cfg.load_column (default: Electric Demand (kW)); file may use demand/kW, we convert to kWh.
    """
    file_path = Path(cfg.csv_path) # Get the file path from the configuration
    if not file_path.exists(): # If the file path does not exist, raise an error
        raise FileNotFoundError(f"Energy load file not found: {file_path}")

    suffix = file_path.suffix.lower() # Get the suffix of the file path
    if suffix in _EXCEL_EXTENSIONS:
        fieldnames, rows = _load_rows_from_excel(file_path, cfg) # Load the rows from the excel file
    else:
        fieldnames, rows = _load_rows_from_csv(file_path) # Load the rows from the csv file 

    if cfg.datetime_column not in fieldnames: # If the datetime column is not in the fieldnames, raise an error 
        raise ValueError(
            f"Missing required datetime column '{cfg.datetime_column}' in {file_path}. "
            f"Found columns: {fieldnames}"
        )
    load_columns = _resolve_load_columns( # Resolve the load columns
        fieldnames=fieldnames,
        configured_column=cfg.load_column,
        csv_path=file_path,
    )
    series_values: dict[str, list[float]] = {col: [] for col in load_columns} # Initialize the series values
    series_units = {col: _infer_units_from_header(col) for col in load_columns} # Infer the units from the header
    effective_datetime_format: str | None = ( # Determine the effective datetime format based on the configuration
        None if (cfg.datetime_format is None or cfg.datetime_format == "auto" or cfg.datetime_format == "") else cfg.datetime_format
    )
    datetimes: list[datetime] = [] # Initialize the datetimes           

    for row_idx, row in enumerate(rows, start=2): # Iterate over the rows
        dt_raw = row.get(cfg.datetime_column) # Get the datetime column from the row
        if dt_raw is None or (isinstance(dt_raw, float) and pd.isna(dt_raw)): # If the datetime column is not found or is NaN, continue
            continue
        if isinstance(dt_raw, str) and not dt_raw.strip(): # If the datetime column is an empty string, continue
            continue

        if effective_datetime_format is None: # If the effective datetime format is not set, determine it based on the datetime column  
            if isinstance(dt_raw, datetime): # If the datetime column is a datetime, set the effective datetime format to the default format        
                effective_datetime_format = "%Y-%m-%d %H:%M:%S"  # placeholder for native datetime
            else:
                try:
                    serial = float(dt_raw) # If the datetime column is a float, parse it as a serial number
                    effective_datetime_format = ( # Determine the effective datetime format based on the serial number  (matlab_serial if greater than the typical matlab serial number, otherwise excel_serial)        
                        "matlab_serial"
                        if serial >= _SERIAL_MATLAB_MIN # If the serial number is greater than the typical matlab serial number, set the effective datetime format to matlab_serial
                        else "excel_serial" # Otherwise, set the effective datetime format to excel_serial
                    )
                except (ValueError, TypeError): # If the datetime column is not a float, raise an error
                    effective_datetime_format = "%m/%d/%Y %H:%M" # If the datetime column is not a float, set the effective datetime format to the default format
        fmt = effective_datetime_format or cfg.datetime_format or "auto" # Determine the effective datetime format based on the configuration
        dt = _parse_datetime_cell(dt_raw, fmt, row_idx, file_path) # Parse the datetime column

        parsed_row_values: dict[str, float] = {} # Initialize the parsed row values
        for col in load_columns: # Iterate over the load columns
            load_val = row.get(col) # Get the load value from the row
            if load_val is None or (isinstance(load_val, float) and pd.isna(load_val)): # If the load value is not found or is NaN, continue
                continue
            if isinstance(load_val, str) and not load_val.strip(): # If the load value is an empty string, continue
                continue
            try:
                parsed_row_values[col] = float(load_val) # Parse the load value as a float
            except (ValueError, TypeError) as exc:
                raise ValueError( # If the load value is not a float, raise an error
                    f"Row {row_idx}: failed float parse for column '{col}' value '{load_val!r}'"
                ) from exc

        # Keep datetime rows even when all load values are missing so interpolation can restore gaps.
        fill_missing = math.nan
        datetimes.append(dt) # Add the datetime to the datetimes list
        for col in load_columns:
            series_values[col].append(parsed_row_values.get(col, fill_missing)) # Add the load value to the series values

    if not datetimes: # If no datetimes are present, raise an error
        raise ValueError(f"No load rows were parsed from {file_path}") # If no datetimes are present, raise an error    

    # Ensure chronological order if source rows are not sorted.
    series_matrix = [series_values[col] for col in load_columns] # Create a matrix of the series values
    paired_rows = list(zip(datetimes, *series_matrix)) # Pair the datetimes with the series values
    paired_rows.sort(key=lambda x: x[0]) # Sort the paired rows by the datetime
    datetimes = [row[0] for row in paired_rows] # Get the datetimes from the paired rows
    sorted_series = { # Create a dictionary of the sorted series
        col: [row[idx + 1] for row in paired_rows] # Get the series values from the paired rows
        for idx, col in enumerate(load_columns) # Iterate over the load columns
    }

    # Time conditioning: regularize timestamps, fill NaN/negative via interpolation.
    datetimes, sorted_series = _condition_time_series(datetimes, sorted_series, cfg) # Condition the time series

    # Basic regular-step check (used by downstream resampling/alignment slices).
    dt_hours = None
    if len(datetimes) >= 2:
        dt_seconds = (datetimes[1] - datetimes[0]).total_seconds()
        dt_hours = dt_seconds / 3600.0

    # Convert kW (power) to kWh (energy) for model: Energy = Power × time_step_hours.
    if dt_hours is not None:
        for col in load_columns:
            if series_units[col] == "kW":
                sorted_series[col] = [v * dt_hours for v in sorted_series[col]]
                series_units[col] = "kWh"

    # One series per load column: electricity_load__{suffix}. Model uses all of them (1 or N).
    load_keys: list[str] = []
    timeseries: dict[str, list[float]] = {
        "datetime": datetimes,
        "time_serial": [_datetime_to_matlab_serial(dt) for dt in datetimes],
    }
    for col in load_columns:
        suffix = _normalize_series_key(col)
        key = f"electricity_load__{suffix}"
        load_keys.append(key)
        timeseries[key] = sorted_series[col]

    # Model and data object always use kWh (we convert kW→kWh when needed).
    container = DataContainer(
        indices={"time": list(range(len(datetimes)))},
        timeseries=timeseries,
        static={
            "time_step_hours": dt_hours,
            "load_units": "kWh",
            "load_units_by_series": {
                _normalize_series_key(col): "kWh" for col in load_columns
            },
            "load_columns": load_columns,
            "electricity_load_keys": load_keys,
        },
        tech_params={},
    )
    container.validate_minimum_fields()
    return container

