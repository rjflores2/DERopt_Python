"""Quarterly evidence: solar PV pipeline (raw file → loader → model timesteps).

Mirrors ``load_solar_into_container`` / ``_load_resource_profile_file_into_container`` in
``data_loading/loaders/resource_profiles.py``: numeric coercion, time-of-year alignment,
``treat_negative_as_missing`` (negatives masked before interpolation), index interpolation +
ffill/bfill on the source axis, ``numpy.interp`` onto each load timestamp’s minute-of-year,
then a second negative mask + interpolation + fill on the target series, then multiply by
``time_step_hours`` for ``kWh/kW`` stored in ``DataContainer``.

**Plots** require matplotlib::

    pip install matplotlib

Optional dev extra (see ``pyproject.toml`` ``[project.optional-dependencies] dev``) may
include matplotlib; CSV is always written if the loaders succeed.

Run from repository root::

    python scripts/quarterly_solar_evidence.py
    python scripts/quarterly_solar_evidence.py --load-csv path/to/load.csv \\
        --solar-csv path/to/solar.csv --date 2022-01-01

Outputs (default ``artifacts/quarterly_evidence/``; parent ``artifacts/`` is gitignored):

- ``solar_pipeline_evidence.csv`` — model-aligned rows with raw/coerced flags and pipeline CF stages
- ``solar_evidence_day_raw_vs_cleaned.png`` — one calendar day: single-pane raw vs final CF
- ``solar_evidence_identity.png`` — scatter CF×dt vs stored series (sanity check)

Environment (optional; used when CLI paths are omitted):

- ``DEROPT_LOAD_CSV``, ``DEROPT_SOLAR_CSV``

Built-in temp fixture (no CLI paths): solar CSV uses a fixed user-provided 24-hour diurnal
capacity-factor curve before loader masking/interpolation, with deliberate negatives and
missing cells for stress coverage.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import numpy as np
import pandas as pd

from config.case_config import EnergyLoadFileConfig
from data_loading.loaders.energy_load import load_energy_load
from data_loading.loaders.resource_profiles import (
    _coerce_resource_value_columns_to_numeric,
    _linear_interpolate_series_to_target_minutes_trace,
    _normalize_series_key,
    _read_profile_minutes_frame,
    _select_numeric_resource_columns,
    _time_of_year_minutes,
    load_solar_into_container,
)


def _default_out_dir() -> Path:
    return _project_root / "artifacts" / "quarterly_evidence"


def _fixture_raw_solar_capacity_factors_24h() -> list[float]:
    """24 hourly raw CF values (pre loader); stress rows overridden when writing CSV."""
    # One trailing midnight zero (24 steps); aligns with hourly load rows 0..23.
    return [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.013688,
        0.044936,
        0.102899,
        0.231585,
        0.468075,
        0.611839,
        0.717793,
        0.771196,
        0.787337,
        0.756282,
        0.685719,
        0.56495,
        0.415077,
        0.233785,
        0.074522,
        0.023956,
        0.003264,
        0.0,
    ]


def _write_minimal_fixture_dir() -> tuple[Path, Path]:
    """Return (load_csv, solar_csv) with one full calendar day (hourly) including NaNs and negatives."""
    base = _project_root / "artifacts" / "quarterly_evidence"
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="deropt_solar_evidence_", dir=str(base)))
    load_path = tmp / "loads.csv"
    load_lines = ["Date,Electric Demand (kW)"]
    for h in range(24):
        load_lines.append(f"1/1/2022 {h}:00,10.0")
    load_path.write_text("\n".join(load_lines) + "\n", encoding="utf-8")

    base_cf = _fixture_raw_solar_capacity_factors_24h()
    # Stress at same timesteps as listed base CF magnitudes.
    stress_neg = {0.044936, 0.468075, 0.756282}
    stress_nan_empty = {0.231585, 0.415077}
    stress_nan_literal = {0.717793}

    solar_path = tmp / "solar.csv"
    solar_lines = ["Date,Capacity Factor"]
    for h, cf in enumerate(base_cf):
        ts = f"1/1/2020 {h}:00"
        if cf in stress_neg:
            val = str(-cf)
        elif cf in stress_nan_empty:
            val = ""
        elif cf in stress_nan_literal:
            val = "NaN"
        else:
            val = "0" if cf == 0.0 else repr(cf)
        solar_lines.append(f"{ts},{val}")
    solar_path.write_text("\n".join(solar_lines) + "\n", encoding="utf-8")
    return load_path, solar_path


def _resolve_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path, list[str] | None]:
    load_p = args.load_csv or (os.environ.get("DEROPT_LOAD_CSV") or "").strip()
    solar_p = args.solar_csv or (os.environ.get("DEROPT_SOLAR_CSV") or "").strip()
    solar_cols = None
    if args.solar_column:
        solar_cols = [args.solar_column]
    if load_p and solar_p:
        return Path(load_p), Path(solar_p), solar_cols
    if load_p or solar_p:
        raise SystemExit(
            "Provide both --load-csv and --solar-csv, or set both DEROPT_LOAD_CSV and "
            "DEROPT_SOLAR_CSV, or omit both to use the built-in temp fixture."
        )
    load_path, solar_path = _write_minimal_fixture_dir()
    return load_path, solar_path, solar_cols


def _pre_coerce_text_by_toy(profile_pre: pd.DataFrame, col: str) -> pd.Series:
    """One string per time-of-year minute (duplicate rows joined with '|')."""

    def join_cells(s: pd.Series) -> str:
        parts: list[str] = []
        for v in s:
            if pd.isna(v):
                parts.append("")
            else:
                parts.append(str(v).strip())
        return "|".join(parts)

    ser = profile_pre[col]
    return ser.groupby(ser.index).apply(join_cells).sort_index()


def _parse_window(
    args: argparse.Namespace,
    datetimes: list[datetime],
) -> tuple[datetime, datetime, str]:
    """Return [start, end) with span at least one day; third value is human-readable note."""
    if not datetimes:
        raise SystemExit("No load datetimes in container.")

    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime(day.year, day.month, day.day, 0, 0, 0)
        end = start + timedelta(days=1)
        note = (
            f"Calendar window [{start.isoformat(sep=' ')}, {end.isoformat(sep=' ')}) "
            f"from --date {args.date!r} (naive local timestamps; model step axis)."
        )
        return start, end, note

    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("Use both --start and --end together, or use --date.")
        start = pd.Timestamp(args.start).to_pydatetime()
        end = pd.Timestamp(args.end).to_pydatetime()
        if end <= start:
            raise SystemExit("--end must be after --start.")
        if (end - start) < timedelta(days=1) - timedelta(seconds=1):
            raise SystemExit("Window must span at least one full day (end - start >= 1 day).")
        note = (
            f"Window [{start.isoformat(sep=' ')}, {end.isoformat(sep=' ')}) from --start/--end."
        )
        return start, end, note

    first = datetimes[0]
    day = datetime(first.year, first.month, first.day, 0, 0, 0)
    start = day
    end = day + timedelta(days=1)
    note = (
        f"Default calendar day [{start.isoformat(sep=' ')}, {end.isoformat(sep=' ')}) "
        f"from first load timestamp ({first!r})."
    )
    return start, end, note


def _filter_indices(datetimes: list[datetime], start: datetime, end: datetime) -> list[int]:
    out: list[int] = []
    for i, dt in enumerate(datetimes):
        if dt >= start and dt < end:
            out.append(i)
    return out


def _build_evidence_table(
    *,
    datetimes: list[datetime],
    solar_path: Path,
    value_col: str,
    value_columns: list[str] | None,
    solar_key: str,
    processed: list[float],
    dt_hours: float,
    treat_negative_as_missing: bool,
    interpolation_method: str,
    datetime_column: str | None,
    sheet_name: int | str,
) -> pd.DataFrame:
    profile_pre, value_col_names, _time_col = _read_profile_minutes_frame(
        solar_path, datetime_column=datetime_column, sheet_name=sheet_name
    )
    if value_col not in value_col_names:
        raise SystemExit(f"Column {value_col!r} not found in solar file after parse.")

    text_by_toy = _pre_coerce_text_by_toy(profile_pre, value_col)
    profile_df = profile_pre.copy()
    _coerce_resource_value_columns_to_numeric(profile_df, value_col_names)
    numeric_cols = _select_numeric_resource_columns(
        profile_df,
        value_col_names,
        value_columns,
        file_path=solar_path,
        resource_label="Solar",
    )
    if value_col not in numeric_cols:
        raise SystemExit(f"Column {value_col!r} has no numeric values after coercion.")

    target_minutes = np.array([_time_of_year_minutes(dt) for dt in datetimes], dtype=float)
    series = profile_df[value_col]
    traced, meta = _linear_interpolate_series_to_target_minutes_trace(
        series,
        target_minutes,
        interpolation_method=interpolation_method,
        treat_negative_as_missing=treat_negative_as_missing,
    )

    exact_coerced = meta["exact_toy_coerced_cf"]
    flag_nan = meta["flag_nan_exact_toy"].astype(int)
    flag_neg = meta["flag_negative_exact_toy"].astype(int)
    after_np = meta["cf_after_np_interp"]
    after_second = meta["cf_after_second_neg_mask"]
    final_cf = np.asarray([float(v) for v in traced], dtype=float)

    processed_arr = np.asarray(processed, dtype=float)
    model_cf_from_container = processed_arr / float(dt_hours)

    rt = text_by_toy.reindex(target_minutes)
    raw_text_mapped = ["" if pd.isna(x) else str(x) for x in rt.tolist()]

    n = len(datetimes)
    return pd.DataFrame(
        {
            "model_step_index": np.arange(n, dtype=int),
            "model_datetime": datetimes,
            "time_of_year_minutes": target_minutes,
            "solar_value_column": [value_col] * n,
            "solar_production_key": [solar_key] * n,
            "time_step_hours": [dt_hours] * n,
            "raw_cell_text_before_numeric_coerce": raw_text_mapped,
            "cf_numeric_coerced_exact_toy_match": exact_coerced,
            "flag_nan_after_numeric_coerce": flag_nan,
            "flag_negative_after_numeric_coerce": flag_neg,
            "cf_after_toy_axis_linear_interp": after_np,
            "cf_after_second_negative_mask_pre_fill": after_second,
            "cf_final_aligned_pre_dt_multiplier": final_cf,
            "solar_kwh_per_kw_final": processed_arr,
            "cf_from_container_kwh_over_dt": model_cf_from_container,
            "delta_final_cf_minus_container_cf": final_cf - model_cf_from_container,
        }
    )


def _plot_day_review(df: pd.DataFrame, out_png: Path, window_note: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    t = pd.to_datetime(df["model_datetime"])
    raw = df["cf_numeric_coerced_exact_toy_match"].to_numpy(dtype=float)
    fin = df["cf_final_aligned_pre_dt_multiplier"].to_numpy(dtype=float)
    neg = df["flag_negative_after_numeric_coerce"].to_numpy(dtype=int) != 0
    nanf = df["flag_nan_after_numeric_coerce"].to_numpy(dtype=int) != 0

    fig, ax = plt.subplots(figsize=(12, 5))

    m_raw = np.isfinite(raw)
    ax.scatter(
        t[m_raw],
        raw[m_raw],
        s=36,
        c="#1565C0",
        alpha=0.85,
        label="Coerced CF at exact time-of-year (file; pre mask/interp)",
        zorder=3,
    )
    ax.plot(t, fin, color="#E65100", linewidth=2.0, label="Final CF on model steps (pre ×dt)")
    mneg = neg & np.isfinite(raw)
    if mneg.any():
        ax.scatter(
            t[mneg],
            raw[mneg],
            s=80,
            marker="x",
            color="#C62828",
            linewidths=2,
            label="Negative raw (masked as missing in loader)",
            zorder=4,
        )
    if nanf.any():
        ax.scatter(
            t[nanf],
            np.zeros(np.sum(nanf)),
            s=40,
            marker="s",
            facecolors="none",
            edgecolors="#6A1B9A",
            label="NaN after numeric coerce (exact toy)",
            zorder=4,
        )
    ax.set_ylabel("Capacity factor (–)")
    ax.set_xlabel("Model datetime (naive)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.set_title("Solar pipeline: raw vs final CF (one calendar day)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%m-%d"))
    fig.autofmt_xdate()
    fig.suptitle(window_note, fontsize=9, y=0.02)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def _plot_identity(df: pd.DataFrame, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cf = df["cf_final_aligned_pre_dt_multiplier"].to_numpy(dtype=float)
    dt = float(df["time_step_hours"].iloc[0])
    y = df["solar_kwh_per_kw_final"].to_numpy(dtype=float)
    x = cf * dt
    fig, ax = plt.subplots(figsize=(5, 5))
    lo = min(float(np.nanmin(x)), float(np.nanmin(y)))
    hi = max(float(np.nanmax(x)), float(np.nanmax(y)))
    ax.plot([lo, hi], [lo, hi], "k--", linewidth=1, label="y = x")
    ax.scatter(x, y, s=10, alpha=0.7, color="#2E7D32")
    ax.set_xlabel("CF_final × time_step_hours")
    ax.set_ylabel("Stored solar_kwh_per_kw")
    ax.set_title("Loader identity check")
    ax.legend(loc="upper left")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--load-csv", type=str, default=None, help="Electric load CSV path")
    parser.add_argument("--solar-csv", type=str, default=None, help="Solar resource CSV/XLSX path")
    parser.add_argument(
        "--solar-column",
        type=str,
        default=None,
        help="Optional single solar value column name (passed to loader as solar_columns)",
    )
    parser.add_argument("--out-dir", type=str, default=None, help=f"Output directory (default: {_default_out_dir()})")
    parser.add_argument(
        "--max-rows",
        type=int,
        default=10_000,
        help="Max rows in CSV after windowing (ignored floor: full day when --date or --start/--end)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Calendar day YYYY-MM-DD: output [day 00:00, next day 00:00) in naive load datetimes",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Window start (pandas-parsable); use with --end, span must be >= 1 day",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Window end (exclusive, pandas-parsable)",
    )
    parser.add_argument(
        "--solar-datetime-column",
        type=str,
        default=None,
        help="Optional solar file datetime column name (not passed to loader unless supported)",
    )
    parser.add_argument(
        "--treat-negative-as-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Match load_solar_into_container (default: true)",
    )
    parser.add_argument(
        "--interpolation-method",
        type=str,
        default="linear",
        help="pandas interpolate method on target series (default: linear)",
    )
    parser.add_argument(
        "--sheet-name",
        type=str,
        default="0",
        help="Excel sheet index or name (default 0); ignored for CSV",
    )
    args = parser.parse_args()

    sheet_name: int | str
    try:
        sheet_name = int(args.sheet_name)
    except ValueError:
        sheet_name = args.sheet_name

    load_path, solar_path, solar_cols = _resolve_paths(args)
    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_solar_into_container(
        data,
        solar_path,
        solar_columns=solar_cols,
        datetime_column=args.solar_datetime_column,
        treat_negative_as_missing=args.treat_negative_as_missing,
        interpolation_method=args.interpolation_method,
    )

    keys: list[str] = list(data.static.get("solar_production_keys") or [])
    if not keys:
        raise SystemExit("No solar_production_keys after load_solar_into_container")

    key = keys[0]
    if args.solar_column:
        candidate = f"solar_production__{_normalize_series_key(args.solar_column)}"
        if candidate in data.timeseries:
            key = candidate
        else:
            cols = list(data.static.get("solar_production_columns") or [])
            for k, col in zip(keys, cols, strict=False):
                if str(col).strip() == str(args.solar_column).strip():
                    key = k
                    break

    cols_meta = list(data.static.get("solar_production_columns") or [])
    col_by_key = dict(zip(keys, cols_meta, strict=False)) if cols_meta else {}
    value_col = col_by_key.get(key) or (key.split("__", 1)[1] if "__" in key else key)

    processed = [float(x) for x in data.timeseries[key]]
    dt_hours = float(data.static.get("time_step_hours") or 1.0)
    datetimes = list(data.timeseries.get("datetime") or [])

    full_table = _build_evidence_table(
        datetimes=datetimes,
        solar_path=solar_path,
        value_col=str(value_col),
        value_columns=solar_cols,
        solar_key=key,
        processed=processed,
        dt_hours=dt_hours,
        treat_negative_as_missing=args.treat_negative_as_missing,
        interpolation_method=args.interpolation_method,
        datetime_column=args.solar_datetime_column,
        sheet_name=sheet_name,
    )

    start, end, window_note = _parse_window(args, datetimes)
    idx = _filter_indices(datetimes, start, end)
    if not idx:
        raise SystemExit(f"No load rows in window {start} .. {end} (exclusive end).")

    evidence_date = start.date().isoformat()
    sub = full_table.iloc[idx].copy()
    sub.insert(0, "evidence_calendar_date", evidence_date)

    day_rows = len(sub)
    if args.date or (args.start and args.end):
        cap = max(args.max_rows, day_rows) if args.max_rows > 0 else day_rows
    else:
        cap = args.max_rows if args.max_rows > 0 else day_rows
    if cap < len(sub):
        sub = sub.iloc[:cap]

    csv_path = out_dir / "solar_pipeline_evidence.csv"
    sub.to_csv(csv_path, index=False, na_rep="")

    print(window_note)
    print(
        "Alignment: solar values are interpolated in **time-of-year minutes** from the solar "
        "file onto each **model load datetime** (see _time_of_year_minutes in resource_profiles). "
        "Plot x-axis is model_datetime (naive). Solar file timestamps define source rows; "
        "cf_numeric_coerced_exact_toy_match is the file value only where a row exists for that "
        "exact minute-of-year key (else NaN/empty in CSV)."
    )
    print(f"evidence_calendar_date={evidence_date!r} rows_in_window={day_rows} rows_written={len(sub)}")

    try:
        _plot_day_review(sub, out_dir / "solar_evidence_day_raw_vs_cleaned.png", window_note)
        _plot_identity(sub, out_dir / "solar_evidence_identity.png")
        plots_msg = "Wrote solar_evidence_day_raw_vs_cleaned.png and solar_evidence_identity.png"
    except ImportError:
        plots_msg = "matplotlib not installed; skipped PNG plots (pip install matplotlib)"

    print(f"Wrote {csv_path}")
    print(plots_msg)
    print(f"solar_production_key={key!r} n_full_series={len(processed)} time_step_hours={dt_hours}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
