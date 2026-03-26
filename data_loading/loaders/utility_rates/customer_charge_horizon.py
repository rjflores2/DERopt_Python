"""Horizon scaling for usage-independent utility customer charges (fixed meter, minimum, etc.).

OpenEI-style JSON exposes amounts with unit strings such as ``$/day`` and ``$/month``.
This module converts those to total **USD over the simulation window** using the model's
time series of datetimes.

* **Daily** charges: ``amount × (number of distinct calendar days with at least one timestep)``.
* **Monthly** charges: for each calendar month that overlaps the simulation, charge is
  ``amount × (days represented in that month / days in that calendar month)``.

  Proration supports partial months (e.g. service for 10 days in January → ``10/31`` of
  the monthly fee). This also supports future modeling of connect/disconnect mid-period.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any, Literal

ChargeBasis = Literal["daily", "monthly"]


def _as_date(dt: datetime | date) -> date:
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def _days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _classify_units(units: str) -> ChargeBasis:
    """Map OpenEI unit strings to daily vs monthly."""
    u = units.strip().lower().replace("$", "").replace(" ", "")
    if u in ("/day", "/d", "day", "perday"):
        return "daily"
    if u in ("/month", "/mo", "month", "permonth"):
        return "monthly"
    if "day" in u and "month" not in u:
        return "daily"
    if "month" in u:
        return "monthly"
    raise ValueError(f"Unrecognized fixed-charge units: {units!r} (expected e.g. '$/day' or '$/month')")


def _daily_component_usd(amount: float, distinct_dates: set[date]) -> float:
    return amount * float(len(distinct_dates))


def _monthly_prorated_component_usd(amount: float, distinct_dates: set[date]) -> float:
    """Prorate each calendar month by fraction of days in that month that appear in the run."""
    if not distinct_dates:
        return 0.0
    # Group distinct simulation dates by (year, month)
    by_ym: dict[tuple[int, int], set[date]] = {}
    for d in distinct_dates:
        by_ym.setdefault((d.year, d.month), set()).add(d)
    total = 0.0
    for (y, m), days_in_run in by_ym.items():
        dim = _days_in_month(y, m)
        overlap_days = len(days_in_run)
        total += amount * (overlap_days / float(dim))
    return total


def _component_usd(amount: float, units: str, distinct_dates: set[date]) -> float:
    basis = _classify_units(units)
    if basis == "daily":
        return _daily_component_usd(amount, distinct_dates)
    return _monthly_prorated_component_usd(amount, distinct_dates)


def fixed_customer_charges_horizon_usd(
    customer_fixed_charges: dict[str, Any] | None,
    datetimes: list[datetime | None] | None,
) -> float:
    """
    Total fixed customer-charge USD over the horizon represented by ``datetimes``.

    Sums all supported entries in ``customer_fixed_charges`` (e.g. ``first_meter``,
    ``minimum``), each with ``amount`` and ``units``.

    Timesteps with ``datetime is None`` are skipped. If no valid timestamps remain,
    returns ``0.0``.

    Raises:
        ValueError: if a non-zero charge component has missing or unrecognized ``units``,
            or a non-numeric ``amount``. Bad tariff data must be fixed explicitly; the
            model does not skip or guess.
    """
    if not customer_fixed_charges:
        return 0.0
    if not datetimes:
        return 0.0

    distinct_dates: set[date] = set()
    for dt in datetimes:
        if dt is None:
            continue
        distinct_dates.add(_as_date(dt))

    if not distinct_dates:
        return 0.0

    out = 0.0
    for key in ("first_meter", "minimum"):
        comp = customer_fixed_charges.get(key)
        if not isinstance(comp, dict):
            continue
        if "amount" not in comp:
            continue
        try:
            amt = float(comp["amount"])
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"customer_fixed_charges[{key!r}]: invalid amount {comp.get('amount')!r}"
            ) from e
        if amt == 0.0:
            continue
        units = comp.get("units")
        if units is None or str(units).strip() == "":
            raise ValueError(
                f"customer_fixed_charges[{key!r}]: non-zero amount requires non-empty 'units' (got {units!r})"
            )
        out += _component_usd(amt, str(units), distinct_dates)
    return out
