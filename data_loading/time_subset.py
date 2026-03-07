"""Optional time-horizon subsetting for faster development runs."""

from __future__ import annotations

from dataclasses import dataclass

from data_loading.schemas import DataContainer


@dataclass(slots=True)
class TimeSubsetConfig:
    """Configuration for selecting a subset of timesteps (e.g. a few months or weeks for faster runs).

    - months: keep rows whose datetime.month is in this list (1=Jan .. 12=Dec). E.g. [1, 2] = Jan+Feb.
    - iso_weeks: keep rows whose ISO week number is in this list (1..53). E.g. [1, 2, 3, 4] = first 4 weeks of year.
    - max_steps: after month/week selection, keep only the first max_steps rows (cap total length).
    If both months and iso_weeks are set, rows matching either are kept (union). Use max_steps alone to
    take the first N steps of the full series without filtering by calendar.
    """

    months: list[int] | None = None
    iso_weeks: list[int] | None = None
    max_steps: int | None = None


def apply_time_subset(data: DataContainer, cfg: TimeSubsetConfig) -> DataContainer:
    """Return a copy-like in-place update of data to selected timesteps.

    Selection semantics:
    - months only: keep rows whose datetime.month is in months
    - iso_weeks only: keep rows whose datetime.isocalendar().week is in iso_weeks
    - both set: keep rows matching either condition (union)
    - max_steps: truncate selected rows to first max_steps rows
    """
    datetimes = data.timeseries.get("datetime")
    if not isinstance(datetimes, list) or not datetimes:
        raise ValueError("time subsetting requires non-empty timeseries['datetime']")

    keep_months = set(cfg.months or [])
    keep_weeks = set(cfg.iso_weeks or [])
    if not keep_months and not keep_weeks and cfg.max_steps is None:
        return data

    for month in keep_months:
        if month < 1 or month > 12:
            raise ValueError(f"months must be 1..12; got {month}")
    for week in keep_weeks:
        if week < 1 or week > 53:
            raise ValueError(f"iso_weeks must be 1..53; got {week}")
    if cfg.max_steps is not None and cfg.max_steps <= 0:
        raise ValueError(f"max_steps must be > 0; got {cfg.max_steps}")

    keep_idx: list[int] = []
    for i, dt in enumerate(datetimes):
        month_match = bool(keep_months) and (dt.month in keep_months)
        week_match = bool(keep_weeks) and (dt.isocalendar().week in keep_weeks)
        if keep_months or keep_weeks:
            if month_match or week_match:
                keep_idx.append(i)
        else:
            keep_idx.append(i)

    if cfg.max_steps is not None:
        keep_idx = keep_idx[: cfg.max_steps]

    if not keep_idx:
        raise ValueError("time subsetting produced zero rows; relax months/weeks/max_steps")

    original_len = len(data.indices.get("time", []))
    if original_len <= 0:
        original_len = len(datetimes)

    for key, values in list(data.timeseries.items()):
        if isinstance(values, list) and len(values) == original_len:
            data.timeseries[key] = [values[i] for i in keep_idx]

    if getattr(data, "import_prices", None) is not None and len(data.import_prices) == original_len:
        data.import_prices = [data.import_prices[i] for i in keep_idx]

    data.indices["time"] = list(range(len(keep_idx)))
    data.static["time_subset_applied"] = {
        "months": sorted(keep_months) if keep_months else [],
        "iso_weeks": sorted(keep_weeks) if keep_weeks else [],
        "max_steps": cfg.max_steps,
        "rows_kept": len(keep_idx),
        "rows_original": original_len,
    }
    return data
