"""CSV loader for generic energy demand timeseries."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from config.case_config import EnergyLoadFileConfig
from data_loading.schemas import DataContainer


def _to_matlab_serial(dt: datetime) -> float:
    """Convert Python datetime to MATLAB-style serial day number."""
    midnight = datetime(dt.year, dt.month, dt.day)
    seconds = (dt - midnight).total_seconds()
    return float(dt.toordinal() + 366) + (seconds / 86400.0)


_UNIT_IN_PARENS_RE = re.compile(r"\([^)]*\bkw(?:h)?\b[^)]*\)", re.IGNORECASE)
_KW_RE = re.compile(r"\bkw\b", re.IGNORECASE)
_KWH_RE = re.compile(r"\bkwh\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_series_key(header_name: str) -> str:
    """Convert header text to a stable key suffix."""
    return _NON_ALNUM_RE.sub("_", header_name.lower()).strip("_")


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
    """Resolve load columns by explicit/fallback unit-based detection."""
    matched_unit_columns = [name for name in fieldnames if _UNIT_IN_PARENS_RE.search(name)]
    selected: list[str] = []

    if configured_column in fieldnames:
        selected.append(configured_column)
        selected.extend([c for c in matched_unit_columns if c != configured_column])
        return selected

    if matched_unit_columns:
        return matched_unit_columns

    raise ValueError(
        f"Missing required load column '{configured_column}' in {csv_path}. "
        f"No fallback '(kW)'/'(kWh)' column was detected. Found columns: {fieldnames}"
    )


def _infer_units_from_header(header_name: str) -> str:
    """Infer load units from selected header name."""
    if _KWH_RE.search(header_name):
        return "kWh"
    if _KW_RE.search(header_name):
        return "kW"
    return "unknown"


def load_energy_demand_csv(cfg: EnergyLoadFileConfig) -> DataContainer:
    """Load generic energy demand CSV into a DataContainer.

    Expected columns:
    - cfg.datetime_column (default: Date)
    - cfg.load_column (default: Electric Demand (kW))
    """
    csv_path = Path(cfg.csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Energy load CSV not found: {csv_path}")

    datetimes: list[datetime] = []
    load_columns: list[str] = []
    series_values: dict[str, list[float]] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        header_reader = csv.reader(f)
        raw_fieldnames = next(header_reader, None)
        if raw_fieldnames is None:
            raise ValueError(f"No header found in CSV: {csv_path}")
        fieldnames = _deduplicate_headers(raw_fieldnames)
        reader = csv.DictReader(f, fieldnames=fieldnames)

        if cfg.datetime_column not in fieldnames:
            raise ValueError(
                f"Missing required datetime column '{cfg.datetime_column}' in {csv_path}. "
                f"Found columns: {fieldnames}"
            )
        load_columns = _resolve_load_columns(
            fieldnames=fieldnames,
            configured_column=cfg.load_column,
            csv_path=csv_path,
        )
        series_values = {col: [] for col in load_columns}
        series_units = {col: _infer_units_from_header(col) for col in load_columns}

        for row_idx, row in enumerate(reader, start=2):
            dt_raw = (row.get(cfg.datetime_column) or "").strip()
            if not dt_raw:
                continue

            try:
                dt = datetime.strptime(dt_raw, cfg.datetime_format)
            except ValueError as exc:
                raise ValueError(
                    f"Row {row_idx}: failed datetime parse '{dt_raw}' "
                    f"with format '{cfg.datetime_format}'"
                ) from exc

            parsed_row_values: dict[str, float] = {}
            any_value_present = False
            for col in load_columns:
                load_raw = (row.get(col) or "").strip()
                if load_raw == "":
                    continue
                any_value_present = True
                try:
                    parsed_row_values[col] = float(load_raw)
                except ValueError as exc:
                    raise ValueError(
                        f"Row {row_idx}: failed float parse for column '{col}' value '{load_raw}'"
                    ) from exc

            if not any_value_present:
                continue

            datetimes.append(dt)
            for col in load_columns:
                series_values[col].append(parsed_row_values.get(col, 0.0))

    if not datetimes:
        raise ValueError(f"No load rows were parsed from {csv_path}")

    # Ensure chronological order if source rows are not sorted.
    series_matrix = [series_values[col] for col in load_columns]
    paired_rows = list(zip(datetimes, *series_matrix))
    paired_rows.sort(key=lambda x: x[0])
    datetimes = [row[0] for row in paired_rows]
    sorted_series = {
        col: [row[idx + 1] for row in paired_rows]
        for idx, col in enumerate(load_columns)
    }

    # Basic regular-step check (used by downstream resampling/alignment slices).
    dt_hours = None
    if len(datetimes) >= 2:
        dt_seconds = (datetimes[1] - datetimes[0]).total_seconds()
        dt_hours = dt_seconds / 3600.0

    primary_column = load_columns[0]
    timeseries = {
        "datetime": datetimes,
        "time_serial": [_to_matlab_serial(dt) for dt in datetimes],
        # Backwards-compatible alias expected by existing validation.
        "electricity_demand": sorted_series[primary_column],
    }
    for col in load_columns:
        suffix = _normalize_series_key(col)
        timeseries[f"electricity_demand__{suffix}"] = sorted_series[col]

    unique_units = sorted(set(series_units.values()))
    load_units = unique_units[0] if len(unique_units) == 1 else "mixed"

    container = DataContainer(
        indices={"time": list(range(len(datetimes)))},
        timeseries=timeseries,
        static={
            "time_step_hours": dt_hours,
            "load_units": load_units,
            "load_units_by_series": {
                _normalize_series_key(col): unit for col, unit in series_units.items()
            },
            "load_columns": load_columns,
            "primary_load_column": primary_column,
        },
        tech_params={},
    )
    container.validate_minimum_fields()
    return container

