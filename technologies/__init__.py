"""Technology module package for DERopt Python rebuild.

Registry: list of (config_key, register_function). Only technologies listed in
technology_parameters (with non-None value) are included in a run; omit a key
to exclude that technology. Add a new technology by adding its module and one
entry here.

The grid/utility block (imports, energy cost, demand charges) is not part of
this registry: model.core attaches it from utilities.electricity_import_export
when import_prices or demand-charge data are present on the DataContainer.

Technology diagnostics (optional):
  Pre-solve warnings for equipment capital and O&M (negative values, or both zero) are
  contributed via ``TECH_DIAGNOSTICS``: list of (config_key, collect_fn) where
  ``collect_fn(model, data, case_cfg) -> list[str]``. Implement
  ``collect_equipment_cost_diagnostics`` in the technology module (or any stable name),
  add one line here, and use ``technologies.equipment_cost_diagnostics`` helpers if useful.
  No edits to ``utilities/model_diagnostics.py`` are required for each new technology.
"""

from __future__ import annotations

from typing import Any, Callable

from technologies.battery_energy_storage import (
    collect_equipment_cost_diagnostics as collect_battery_equipment_cost_diagnostics,
)
from technologies.battery_energy_storage import register as register_battery_energy_storage
from technologies.solar_pv import (
    collect_equipment_cost_diagnostics as collect_solar_equipment_cost_diagnostics,
)
from technologies.solar_pv import register as register_solar_pv

REGISTRY = [
    ("solar_pv", register_solar_pv),
    ("battery_energy_storage", register_battery_energy_storage),
]

# (config_key, diagnostics collector) — same keys as REGISTRY where diagnostics apply.
TECH_DIAGNOSTICS: list[tuple[str, Callable[[Any, Any, Any], list[str]]]] = [
    ("solar_pv", collect_solar_equipment_cost_diagnostics),
    ("battery_energy_storage", collect_battery_equipment_cost_diagnostics),
]
