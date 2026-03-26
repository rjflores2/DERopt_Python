"""Technology module package for DERopt Python rebuild.

Registry: list of (config_key, register_function). Only technologies listed in
technology_parameters (with non-None value) are included in a run; omit a key
to exclude that technology. Add a new technology by adding its module and one
entry here.

The grid/utility block (imports, energy cost, demand charges) is not part of
this registry: model.core attaches it from utilities.electricity_import_export
when import_prices or demand-charge data are present on the DataContainer.

Technology diagnostics (optional, plug-in style):
  In a technology module (same package as this file), define::

      def collect_equipment_cost_diagnostics(model, data, case_cfg) -> list[str]: ...

  ``utilities.model_diagnostics`` discovers this name automatically via
  ``pkgutil.iter_modules`` (shared helpers live in ``equipment_cost_diagnostics``).
  No change to this ``__init__`` is required when adding a new technology with diagnostics.
"""

from technologies.battery_energy_storage import register as register_battery_energy_storage
from technologies.solar_pv import register as register_solar_pv

REGISTRY = [
    ("solar_pv", register_solar_pv),
    ("battery_energy_storage", register_battery_energy_storage),
]
