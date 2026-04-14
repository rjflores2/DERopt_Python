"""Technology module package for DERopt Python rebuild.

Registry: list of (config_key, register_function). ``build_model`` calls
``register`` only for names where ``technology_parameters.get(config_key)`` is
not ``None`` (missing key = omit). Value ``None`` omits; ``{}`` or a dict
requests the technology: ``register`` must attach ``model.<config_key>`` and
return that same ``Block``, or ``build_model`` raises (resource-dependent tech
cannot silently skip when requested). Add a new technology by adding its module
and one entry here.

The grid/utility block (imports, energy cost, demand charges) is not part of
this registry: ``model.core`` calls ``utilities.electricity_import_export.register``,
which attaches the block only when resolved inputs include energy prices,
demand charges, and/or fixed customer charges; otherwise it may attach nothing.

Technology diagnostics (optional, plug-in style):
  In a technology module (same package as this file), define::

      def collect_equipment_cost_diagnostics(model, data, case_cfg) -> list[str]: ...

  ``utilities.model_diagnostics`` discovers this name automatically via
  ``pkgutil.iter_modules`` (shared helpers live in ``equipment_cost_diagnostics``).
  No change to this ``__init__`` is required when adding a new technology with diagnostics.
"""

from technologies.alkaline_electrolyzer import register as register_alkaline_electrolyzer
from technologies.battery_energy_storage import register as register_battery_energy_storage
from technologies.compressed_gas_hydrogen_storage import register as register_compressed_gas_hydrogen_storage
from technologies.diesel_generator import register as register_diesel_generator
from technologies.flow_battery_energy_storage import register as register_flow_battery_energy_storage
from technologies.hydrokinetic import register as register_hydrokinetic
from technologies.pem_electrolyzer import register as register_pem_electrolyzer
from technologies.pem_fuel_cell import register as register_pem_fuel_cell
from technologies.solar_pv import register as register_solar_pv

REGISTRY = [
    ("solar_pv", register_solar_pv),
    ("battery_energy_storage", register_battery_energy_storage),
    ("flow_battery_energy_storage", register_flow_battery_energy_storage),
    ("diesel_generator", register_diesel_generator),
    ("hydrokinetic", register_hydrokinetic),
    ("pem_electrolyzer", register_pem_electrolyzer),
    ("alkaline_electrolyzer", register_alkaline_electrolyzer),
    ("pem_fuel_cell", register_pem_fuel_cell),
    ("compressed_gas_hydrogen_storage", register_compressed_gas_hydrogen_storage),
]
