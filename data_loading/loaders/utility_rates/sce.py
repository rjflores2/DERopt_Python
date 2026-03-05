"""Southern California Edison (SCE) OpenEI rate loader.

Known semantics:
- TOU: one rate per tier, schedule picks tier by (month, hour). No usage blocks.
- Monthly tiered: usage blocks with max in kWh/day; applied to monthly usage
  as first (max * days_in_month) kWh in block 1, etc. Schedule picks which
  set of blocks (e.g. summer vs non-summer) by (month, hour).
- Demand: when demandratestructure / flatdemandstructure present (e.g. GS-3).
"""

from __future__ import annotations

from data_loading.loaders.utility_rates import ParsedRate, RateType, register_utility


def _is_tiered_structure(energyratestructure: list) -> bool:
    """True if any tier has multiple blocks or blocks with 'max' (usage tiers)."""
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


def _parse_schedule(energyweekdayschedule: list, energyweekendschedule: list | None) -> list[list[int]]:
    """Return 12×24 schedule: schedule[month][hour] = tier index. Month 0–11, hour 0–23."""
    # OpenEI: list of 12 months, each 24 hours.
    week = energyweekdayschedule
    weekend = energyweekendschedule or energyweekdayschedule
    out = []
    for m in range(12):
        row = []
        for h in range(24):
            # OpenEI sometimes uses weekday vs weekend; use weekday as default.
            row.append(week[m][h] if m < len(week) and h < len(week[m]) else 0)
        out.append(row)
    return out


def _extract_demand(item: dict) -> dict | None:
    """If rate has demand component, return normalized structure; else None."""
    demand_struct = item.get("demandratestructure")
    flat_demand = item.get("flatdemandstructure")
    if not demand_struct and not flat_demand:
        return None
    result: dict = {}
    if demand_struct:
        result["demandratestructure"] = demand_struct
        result["demandweekdayschedule"] = item.get("demandweekdayschedule", [])
        result["demandweekendschedule"] = item.get("demandweekendschedule", [])
    if flat_demand:
        result["flatdemandstructure"] = flat_demand
        result["flatdemandmonths"] = item.get("flatdemandmonths", [])
    return result if result else None


@register_utility("Southern California Edison Co")
def load_sce_rate(item: dict) -> ParsedRate:
    """Parse an SCE OpenEI rate item. Detects TOU vs monthly tiered from structure."""
    utility = item.get("utility", "Southern California Edison Co")
    name = item.get("name", "")

    energy = item.get("energyratestructure") or []
    if _is_tiered_structure(energy):
        # SCE tiered residential (e.g. D Tiered): kWh/day blocks, applied monthly.
        schedule = _parse_schedule(
            item.get("energyweekdayschedule", []),
            item.get("energyweekendschedule"),
        )
        # Blocks per tier: list of {max_kwh_per_day, rate, adj?}
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
                "schedule_month_hour": schedule,
                "tiers_blocks": tiers_blocks,
                "energyratestructure": energy,
            },
            demand=_extract_demand(item),
        )
    else:
        # TOU: one rate per tier, schedule picks tier by (month, hour).
        schedule = _parse_schedule(
            item.get("energyweekdayschedule", []),
            item.get("energyweekendschedule"),
        )
        # Hourly price by (month, hour): rate + adj for that tier.
        prices = []
        for m in range(12):
            for h in range(24):
                ti = schedule[m][h] if m < len(schedule) and h < len(schedule[m]) else 0
                tier = energy[ti] if ti < len(energy) else energy[0]
                block = (tier[0] if isinstance(tier, list) and tier else tier) if tier else {}
                rate = block.get("rate", 0) + block.get("adj", 0)
                prices.append(rate)
        return ParsedRate(
            rate_type="tou",
            utility=utility,
            name=name,
            payload={
                "schedule_month_hour": schedule,
                "energyratestructure": energy,
                "prices_12x24": prices,
                "prices_8760": None,
            },
            demand=_extract_demand(item),
        )
