"""Southern California Edison (SCE) OpenEI rate loader.

Known semantics:
- TOU: one rate per tier, schedule picks tier by (month, hour). No usage blocks.
- Monthly tiered: usage blocks with max in kWh/day; applied to monthly usage
  as first (max * days_in_month) kWh in block 1, etc. Schedule picks which
  set of blocks (e.g. summer vs non-summer) by (month, hour).
- Demand charges: when OpenEI demandratestructure / flatdemandstructure present (e.g. GS-3).
  We normalize to demand_charge_* keys in ParsedRate.demand_charges.

Demand charge implementation (for optimization model when grid block exists):
- Flat demand charge: ParsedRate.demand_charges["flat_demand_charge_structure"], flat_demand_charge_applicable_months.
  Defined only by months. P >= grid_import for each hour in applicable months.
- TOU demand charge: demand_charge_ratestructure, demand_charge_weekdayschedule, demand_charge_weekendschedule
  (each 12×24: schedule[month][hour] = tier index). Same constraint but only for hours in each tier. E.g. tier 1 may
  apply summer weekday 8am–12pm and 6pm–9pm; tier 2 may apply summer weekday
  12pm–6pm. The model needs all three (months, weekdays vs weekends, hours)
  to build the correct constraint set per tier.
"""

from __future__ import annotations

from datetime import datetime

from data_loading.loaders.utility_rates import ParsedRate, RateType, register_utility

#Tiers have two meanings - inside OpenEI, a tier can refer to a time (i.e., tier 1 is for summeer on-peak, tier 2 is summer mid or off)
# Inside a rate, a tiered structure can be a usage block - a block of kWh at a certain rate. 
def _is_tiered_structure(energyratestructure: list) -> bool:
    """True if any tier has multiple blocks or blocks with 'max' (usage tiers insite the rate)."""
    if not energyratestructure:
        return False
    for tier in energyratestructure:
        if not isinstance(tier, list):
            tier = [tier]
        if len(tier) > 1:
            return True
        if tier and isinstance(tier[0], dict) and "max" in tier[0]:
            return True
    return False


def _fill_schedule(sched: list, default: int = 0) -> list[list[int]]:
    """Return 12×24 grid: schedule[month][hour] = tier index. Fills missing with default."""
    out = []
    for m in range(12):
        row = []
        for h in range(24):
            v = default
            if m < len(sched) and h < len(sched[m]):
                v = sched[m][h]
            row.append(v)
        out.append(row)
    return out


def _parse_schedule(
    energyweekdayschedule: list,
    energyweekendschedule: list | None,
) -> tuple[list[list[int]], list[list[int]]]:
    """Return (weekday_schedule, weekend_schedule), each 12×24: schedule[month][hour] = tier index."""
    has_weekday = bool(energyweekdayschedule)
    has_weekend = energyweekendschedule is not None and bool(energyweekendschedule)
    if has_weekday != has_weekend:
        raise ValueError(
            "SCE rate must have both energyweekdayschedule and energyweekendschedule, or neither. "
            f"Got weekday={bool(energyweekdayschedule)!r}, weekend={energyweekendschedule is not None!r}."
        )
    week = energyweekdayschedule or []
    weekend = energyweekendschedule if energyweekendschedule is not None else week
    return _fill_schedule(week), _fill_schedule(weekend)


def _extract_demand_charges(item: dict) -> dict | None:
    """If rate has demand-charge component, return normalized structure for model.utility block; else None."""
    tou_demand_charge_struct = item.get("demandratestructure")
    flat_demand_charge_struct = item.get("flatdemandstructure")
    if not tou_demand_charge_struct and not flat_demand_charge_struct:
        return None
    result: dict = {}
    if tou_demand_charge_struct:
        result["demand_charge_type"] = "tou"
        result["demand_charge_ratestructure"] = tou_demand_charge_struct
        # Normalize to 12×24 for model: schedule[month][hour] = tier index
        result["demand_charge_weekdayschedule"] = _fill_schedule(item.get("demandweekdayschedule", []))
        result["demand_charge_weekendschedule"] = _fill_schedule(
            item.get("demandweekendschedule") or item.get("demandweekdayschedule", [])
        )
    if flat_demand_charge_struct:
        if "demand_charge_type" in result:
            result["demand_charge_type"] = "both"
        else:
            result["demand_charge_type"] = "flat"
        result["flat_demand_charge_structure"] = flat_demand_charge_struct
        flat_months = item.get("flatdemandmonths", [])
        result["flat_demand_charge_months"] = flat_months
        # Month indices (0–11) where flat demand charge applies; model uses for constraint set
        result["flat_demand_charge_applicable_months"] = [m for m in range(12) if m < len(flat_months) and flat_months[m]]
    return result if result else None


def _tou_prices_for_schedule(
    schedule: list[list[int]],
    energy: list,
) -> list[float]:
    """Build 12×24 price list from schedule and energyratestructure. Safe for empty energy (caller must guard)."""
    if not energy:
        return []
    prices = []
    for m in range(12):
        for h in range(24):
            ti = 0
            if m < len(schedule) and h < len(schedule[m]):
                ti = min(schedule[m][h], len(energy) - 1)
            if ti < 0:
                ti = 0
            tier = energy[ti]
            block = (tier[0] if isinstance(tier, list) and tier else tier) if tier else {}
            rate = block.get("rate", 0) + block.get("adj", 0)
            prices.append(rate)
    return prices


def tou_import_prices_for_timestamps(
    import_prices_12x24_weekday: list[float],
    import_prices_12x24_weekend: list[float],
    timestamps: list[datetime],
) -> list[float]:
    """Return import price ($/kWh) for each timestamp using TOU 12×24 weekday/weekend grids.

    Grids are flat 288 (month 0–11 × hour 0–23): index = month * 24 + hour.
    Use the timestamps from building data so weekday/weekend and month match the simulation.
    """
    out: list[float] = []
    for dt in timestamps:
        month = dt.month - 1  # 0–11
        hour_of_day = dt.hour
        is_weekend = dt.weekday() >= 5  # 5=Sat, 6=Sun
        grid = import_prices_12x24_weekend if is_weekend else import_prices_12x24_weekday
        idx = month * 24 + hour_of_day
        out.append(float(grid[idx]) if idx < len(grid) else 0.0)
    return out


@register_utility("Southern California Edison Co")
def load_sce_rate(item: dict) -> ParsedRate:
    """Parse an SCE OpenEI rate item. Detects TOU vs monthly tiered from structure."""
    utility = item.get("utility", "Southern California Edison Co")
    name = item.get("name", "")

    energy = item.get("energyratestructure") or []
    if not energy:
        raise ValueError(
            "SCE rate item has missing or empty 'energyratestructure'. "
            "Cannot determine TOU or tiered structure."
        )

    schedule_weekday, schedule_weekend = _parse_schedule(
        item.get("energyweekdayschedule", []),
        item.get("energyweekendschedule"),
    )

    if _is_tiered_structure(energy):
        # SCE tiered residential (e.g. D Tiered): kWh/day blocks, applied monthly.
        tiers_blocks = []
        for tier in energy:
            blocks = []
            for b in (tier if isinstance(tier, list) else [tier]):
                blk = {"rate": b.get("rate", 0), "adj": b.get("adj", 0)}
                if "max" in b:
                    blk["max_kwh_per_day"] = b["max"]
                blocks.append(blk)
            tiers_blocks.append(blocks)
        return ParsedRate(
            rate_type="monthly_tiered",
            utility=utility,
            name=name,
            payload={
                "tier_period": "monthly",
                "block_direction": "inclining",
                "schedule_month_hour": schedule_weekday,
                "schedule_weekday": schedule_weekday,
                "schedule_weekend": schedule_weekend,
                "tiers_blocks": tiers_blocks,
                "energyratestructure": energy,
            },
            demand_charges=_extract_demand_charges(item),
        )
    else:
        # TOU: one rate per tier; weekday and weekend schedules can differ.
        # Import prices 8760 (or N) are built from building-data timestamps via tou_import_prices_for_timestamps().
        import_prices_12x24_weekday = _tou_prices_for_schedule(schedule_weekday, energy)
        import_prices_12x24_weekend = _tou_prices_for_schedule(schedule_weekend, energy)
        return ParsedRate(
            rate_type="tou",
            utility=utility,
            name=name,
            payload={
                "schedule_month_hour": schedule_weekday,
                "schedule_weekday": schedule_weekday,
                "schedule_weekend": schedule_weekend,
                "energyratestructure": energy,
                "import_prices_12x24": import_prices_12x24_weekday,
                "import_prices_12x24_weekday": import_prices_12x24_weekday,
                "import_prices_12x24_weekend": import_prices_12x24_weekend,
            },
            demand_charges=_extract_demand_charges(item),
        )
