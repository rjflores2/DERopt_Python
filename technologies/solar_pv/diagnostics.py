"""Solar PV diagnostics hooks."""

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
    """Warn on negative or all-zero solar capital / O&M per profile."""
    if not hasattr(model, "solar_pv"):
        return []

    blk = model.solar_pv
    out: list[str] = []
    for profile in blk.SOLAR:
        cap = float(pyo.value(blk.capital_cost_per_kw[profile]))
        om = float(pyo.value(blk.om_per_kw_year[profile]))
        out.extend(
            equipment_capital_om_warnings(
                f"Solar profile {str(profile)!r}",
                cap,
                om,
                capital_name="capital_cost_per_kw",
                om_name="om_per_kw_year",
            )
        )
    return out
