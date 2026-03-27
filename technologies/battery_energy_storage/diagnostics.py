"""Battery diagnostics hooks."""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from technologies.equipment_cost_diagnostics import equipment_capital_om_warnings


def collect_equipment_cost_diagnostics(
    model: Any,
    _data: Any,
    _case_cfg: Any,
) -> list[str]:
    """Warn on negative or all-zero battery capital / O&M (values from built model)."""
    if not hasattr(model, "battery_energy_storage"):
        return []

    blk = model.battery_energy_storage
    try:
        cap = float(pyo.value(blk.capital_cost_per_kwh))
        om = float(pyo.value(blk.om_per_kwh_year))
    except (TypeError, ValueError):
        return [
            "battery_energy_storage: could not read capital_cost_per_kwh / om_per_kwh_year from model for diagnostics."
        ]
    return equipment_capital_om_warnings(
        "Battery",
        cap,
        om,
        capital_name="capital_cost_per_kwh",
        om_name="om_per_kwh_year",
    )
