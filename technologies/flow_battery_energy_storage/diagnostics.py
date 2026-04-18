"""Flow battery diagnostics hooks."""

from __future__ import annotations

import pyomo.environ as pyo

from config.case_config import CaseConfig
from data_loading.schemas import DataContainer
from technologies.equipment_cost_diagnostics import equipment_capital_om_warnings


def collect_equipment_cost_diagnostics(
    model: pyo.Block,
    _data: DataContainer,
    _case_cfg: CaseConfig | None,
) -> list[str]:
    """Warn on negative or all-zero flow-battery capital / O&M (energy and power sides)."""
    if not hasattr(model, "flow_battery_energy_storage"):
        return []

    blk = model.flow_battery_energy_storage
    out: list[str] = []
    try:
        ec = float(pyo.value(blk.energy_capital_cost_per_kwh))
        eom = float(pyo.value(blk.energy_om_per_kwh_year))
        pc = float(pyo.value(blk.power_capital_cost_per_kw))
        pom = float(pyo.value(blk.power_om_per_kw_year))
    except (TypeError, ValueError):
        return [
            "flow_battery_energy_storage: could not read cost parameters from model for diagnostics."
        ]
    out.extend(
        equipment_capital_om_warnings(
            "Flow battery (energy)",
            ec,
            eom,
            capital_name="energy_capital_cost_per_kwh",
            om_name="energy_om_per_kwh_year",
        )
    )
    out.extend(
        equipment_capital_om_warnings(
            "Flow battery (power)",
            pc,
            pom,
            capital_name="power_capital_cost_per_kw",
            om_name="power_om_per_kw_year",
        )
    )
    return out
