"""Overarching report template: schema and emissions hook."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.reporting import SCHEMA_VERSION, build_overarching_report


def _base_data() -> DataContainer:
    return DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [0.0],
            "solar_p": [0.0],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "solar_production_keys": ["solar_p"],
            "time_step_hours": 1.0,
        },
    )


def test_overarching_report_schema_emissions_placeholder_and_costs_by_technology():
    data = _base_data()
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        customer_fixed_charges={"first_meter": {"amount": 10.0, "units": "$/day"}},
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}
    tech = {
        "solar_pv": {
            "allow_adoption": False,
            "om_per_kw_year": 5.0,
            "existing_capital_recovery_per_kw_year": 3.0,
            "existing_solar_capacity_by_node_and_profile": {("electricity_load__x", "solar_p"): 2.0},
        },
        "battery_energy_storage": {
            "allow_adoption": False,
            "om_per_kwh_year": 4.0,
            "existing_energy_capacity_by_node": {"electricity_load__x": 2.0},
        },
    }
    m = build_model(data, technology_parameters=tech, financials={})
    m.utility.grid_import["electricity_load__x", 0].value = 0.0
    m.solar_pv.solar_generation["electricity_load__x", "solar_p", 0].value = 0.0

    rep = build_overarching_report(m, data)

    assert rep["schema_version"] == SCHEMA_VERSION
    assert rep["emissions"]["status"] == "not_modeled"
    assert rep["meta"]["n_timesteps"] == 1
    assert rep["meta"]["nodes"] == ["electricity_load__x"]
    assert "solar_pv" in rep["costs"]["by_technology"]
    assert rep["costs"]["by_technology"]["solar_pv"]["cost_non_optimizing_annual"] == pytest.approx(16.0)
    assert rep["costs"]["by_technology"]["utility"]["cost_non_optimizing_annual"] == pytest.approx(10.0)


def test_overarching_report_emissions_provider():
    data = _base_data()
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        customer_fixed_charges={"first_meter": {"amount": 1.0, "units": "$/day"}},
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}
    m = build_model(data, technology_parameters={}, financials={})
    m.utility.grid_import["electricity_load__x", 0].value = 0.0

    def provider(_model, _data):
        return {"aggregate": {"co2e_kg": 42.0}, "by_technology": {"grid": {"co2e_kg": 42.0}}}

    rep = build_overarching_report(m, data, emissions_provider=provider)
    assert rep["emissions"]["status"] == "provided"
    assert rep["emissions"]["aggregate"]["co2e_kg"] == 42.0
