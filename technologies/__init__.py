"""Technology module package for DERopt Python rebuild.

Registry: list of (config_key, register_function). Only technologies listed in
technology_parameters (with non-None value) are included in a run; omit a key
to exclude that technology. Add a new technology by adding its module and one
entry here.

The grid/utility block (imports, energy cost, demand charges) is not part of
this registry: model.core attaches it from utilities.electricity_import_export
when import_prices or demand-charge data are present on the DataContainer.
"""

from technologies.battery_energy_storage import register as register_battery_energy_storage
from technologies.solar_pv import register as register_solar_pv

REGISTRY = [
    ("solar_pv", register_solar_pv),
    ("battery_energy_storage", register_battery_energy_storage),
]
