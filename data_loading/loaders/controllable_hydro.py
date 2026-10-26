"""Loader for controllable_hydro per-unit volumetric inflow series.

Reads a single reservoir/unit's inflow record (cfs or acre-feet) from CSV/Excel, aligns it
to the container's time index the same way ``resource_profiles.py`` aligns solar/wind/
hydrokinetic resource files, converts it to kWh of stored energy potential via a supplied
``kwh_per_acre_foot`` coefficient, and registers the two static keys the
``controllable_hydro`` block requires (see ``technologies/controllable_hydro/__init__.py``
and ``requirements/controllable_hydro_spec.md`` section 9). The block itself never sees
volumetric units -- that conversion happens only here.

Call once per unit; unit keys and inflow-series keys accumulate across calls (via
``data.static["controllable_hydro_unit_keys"]`` / ``["controllable_hydro_inflow_keys"]``) so
a multi-reservoir case can be built up incrementally, one file per site.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from data_loading.loaders.resource_profiles import (
    _coerce_resource_value_columns_to_numeric,
    _linear_interpolate_series_to_target_minutes,
    _raise_if_aligned_contains_nan,
    _read_profile_minutes_frame,
    _select_numeric_resource_columns,
    _time_of_year_minutes,
)
from data_loading.schemas import DataContainer

VOLUME_UNITS_ACRE_FEET = "acre_feet"
VOLUME_UNITS_CFS = "cfs"

# 1 acre-foot = 43,560 ft^3. 1 cfs = 1 ft^3/s = 3,600 ft^3/hr, so 1 cfs-hour = 3600/43560
# acre-feet (equivalently, 1 acre-foot = 12.1 cfs-hours). Standard hydrology constant.
_ACRE_FEET_PER_CFS_HOUR = 3600.0 / 43560.0


def load_controllable_hydro_inflow_into_container(
    data: DataContainer,
    inflow_path: Path,
    *,
    unit_key: str,
    kwh_per_acre_foot: float,
    volume_units: str = VOLUME_UNITS_ACRE_FEET,
    datetime_column: str | None = None,
    value_column: str | None = None,
    treat_negative_as_missing: bool = True,
    interpolation_method: str = "linear",
    sheet_name: int | str = 0,
) -> str:
    """Load one reservoir/unit's inflow series and register it for ``controllable_hydro``.

    ``volume_units`` selects the conversion path:

    - ``"acre_feet"`` (default): the source column is already a volume per source-record
      interval. No additional scaling by ``time_step_hours`` is applied before the kWh
      conversion.
    - ``"cfs"``: the source column is a flow rate (cubic feet per second). It is converted
      to acre-feet using the standard cfs-hour/acre-foot constant and scaled by
      ``data.static["time_step_hours"]`` to get the volume delivered over one model
      timestep -- the same "rate times timestep duration" pattern
      ``resource_profiles.py`` uses to turn a capacity factor into kWh/kW per interval.

    ``kwh_per_acre_foot`` then converts acre-feet-per-timestep to kWh of stored energy
    potential per timestep. Per ``requirements/controllable_hydro_spec.md`` section 14,
    this coefficient should be set at the upper end of the operating head band: a
    coefficient fixed at a lower head would satisfy a minimum-release constraint (expressed
    in energy terms) while releasing less water than a licence actually requires.

    Returns the ``data.timeseries`` key the aligned/converted series was written under.
    """
    if volume_units not in (VOLUME_UNITS_ACRE_FEET, VOLUME_UNITS_CFS):
        raise ValueError(
            f"controllable_hydro: volume_units must be {VOLUME_UNITS_ACRE_FEET!r} or "
            f"{VOLUME_UNITS_CFS!r}, got {volume_units!r}"
        )
    if kwh_per_acre_foot <= 0:
        raise ValueError(f"controllable_hydro: kwh_per_acre_foot must be > 0, got {kwh_per_acre_foot}")

    file_path = Path(inflow_path)
    if not file_path.exists():
        raise FileNotFoundError(f"controllable_hydro inflow file not found: {file_path}")

    target_datetimes = data.timeseries.get("datetime") or []
    if not target_datetimes:
        raise ValueError("DataContainer has no time vector (timeseries['datetime'] empty)")

    profile_df, value_col_names, _time_col = _read_profile_minutes_frame(
        file_path, datetime_column=datetime_column, sheet_name=sheet_name
    )
    _coerce_resource_value_columns_to_numeric(profile_df, value_col_names)
    value_columns = [value_column] if value_column is not None else None
    numeric_cols = _select_numeric_resource_columns(
        profile_df,
        value_col_names,
        value_columns,
        file_path=file_path,
        resource_label="controllable_hydro inflow",
    )
    if len(numeric_cols) != 1:
        raise ValueError(
            f"controllable_hydro: expected exactly one inflow column in {file_path}, "
            f"found {numeric_cols!r}. Pass value_column= to select one."
        )
    col = numeric_cols[0]

    target_minutes = np.array([_time_of_year_minutes(dt) for dt in target_datetimes], dtype=float)
    aligned = _linear_interpolate_series_to_target_minutes(
        profile_df[col],
        target_minutes,
        interpolation_method=interpolation_method,
        treat_negative_as_missing=treat_negative_as_missing,
    )
    _raise_if_aligned_contains_nan(
        {col: aligned}, file_path=file_path, resource_label="controllable_hydro inflow"
    )

    dt_hours = data.static.get("time_step_hours")
    if dt_hours is None:
        dt_hours = 1.0
    dt_hours = float(dt_hours)

    if volume_units == VOLUME_UNITS_CFS:
        acre_feet_per_step = [v * _ACRE_FEET_PER_CFS_HOUR * dt_hours for v in aligned]
    else:
        acre_feet_per_step = aligned

    kwh_per_step = [v * kwh_per_acre_foot for v in acre_feet_per_step]

    inflow_key = f"controllable_hydro_inflow__{unit_key}"
    data.timeseries[inflow_key] = kwh_per_step

    unit_keys = list(data.static.get("controllable_hydro_unit_keys") or [])
    if unit_key not in unit_keys:
        unit_keys.append(unit_key)
    data.static["controllable_hydro_unit_keys"] = unit_keys

    inflow_keys_by_unit = dict(data.static.get("controllable_hydro_inflow_keys") or {})
    inflow_keys_by_unit[unit_key] = inflow_key
    data.static["controllable_hydro_inflow_keys"] = inflow_keys_by_unit

    return inflow_key
