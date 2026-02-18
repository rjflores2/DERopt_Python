"""Slice 1 scaffold import tests."""

from importlib import import_module


MODULES = [
    "config",
    "data_loading",
    "data_loading.schemas",
    "data_loading.loaders",
    "model",
    "model.core",
    "utilities",
    "utilities.electricity_import_export",
    "utilities.network",
    "shared",
    "shared.financials",
    "technologies",
    "technologies.solar_pv",
    "technologies.wind",
    "technologies.hydrokinetic",
    "technologies.run_of_river",
    "technologies.dam_hydro",
    "technologies.pumped_hydro",
    "technologies.battery_energy_storage",
    "technologies.flow_battery_energy_storage",
    "technologies.diesel_generation",
    "technologies.gas_turbine",
    "technologies.high_temperature_fuel_cell",
    "technologies.pem_electrolyzer",
    "technologies.alkaline_electrolyzer",
    "technologies.compressed_gas_hydrogen_storage",
    "technologies.pem_fuel_cell",
    "run.playground",
]


def test_scaffold_modules_importable():
    """All scaffold modules should import without side effects/errors."""
    for name in MODULES:
        import_module(name)

