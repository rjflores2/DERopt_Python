"""Objective vs reporting cost split tests."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.results import extract_solution


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


def test_utility_fixed_charge_excluded_from_objective_kept_for_reporting():
    data = _base_data()
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        customer_fixed_charges={"first_meter": {"amount": 10.0, "units": "$/day"}},
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}
    m = build_model(data, technology_parameters={}, financials={})
    # Keep unsolved expression evaluation stable.
    m.utility.grid_import["electricity_load__x", 0].value = 0.0

    assert pyo.value(m.utility.objective_contribution) == pytest.approx(0.0)
    assert pyo.value(m.utility.cost_non_optimizing_annual) == pytest.approx(10.0)
    assert pyo.value(m.total_reported_annual_cost) == pytest.approx(10.0)


def test_existing_solar_costs_reporting_only_when_no_adoption():
    data = _base_data()
    tech = {
        "solar_pv": {
            "allow_adoption": False,
            "om_per_kw_year": 5.0,
            "existing_capital_recovery_per_kw_year": 3.0,
            "existing_solar_capacity_by_node_and_profile": {("electricity_load__x", "solar_p"): 2.0},
        }
    }
    m = build_model(data, technology_parameters=tech, financials={})
    # (5 + 3) * 2 = 16 reporting-only, objective 0.
    assert pyo.value(m.solar_pv.objective_contribution) == pytest.approx(0.0)
    assert pyo.value(m.solar_pv.cost_non_optimizing_annual) == pytest.approx(16.0)
    assert pyo.value(m.total_reported_annual_cost) == pytest.approx(16.0)


def test_existing_battery_costs_reporting_only_when_no_adoption():
    data = _base_data()
    tech = {
        "battery_energy_storage": {
            "allow_adoption": False,
            "om_per_kwh_year": 4.0,
            "existing_energy_capacity_by_node": {"electricity_load__x": 2.0},
        }
    }
    m = build_model(data, technology_parameters=tech, financials={})
    assert pyo.value(m.battery_energy_storage.objective_contribution) == pytest.approx(0.0)
    assert pyo.value(m.battery_energy_storage.cost_non_optimizing_annual) == pytest.approx(8.0)
    assert pyo.value(m.total_reported_annual_cost) == pytest.approx(8.0)


def test_fixed_background_only_case_reports_total_even_with_zero_objective():
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
    extracted = extract_solution(m, data)

    assert extracted["objective_value"] == pytest.approx(0.0)
    cb = extracted["cost_breakdown"]
    assert cb["optimizing_cost"] == pytest.approx(0.0)
    assert cb["fixed_non_optimizing_cost"] == pytest.approx(34.0)  # 10 + 16 + 8
    assert cb["total_reported_cost"] == pytest.approx(34.0)
    assert cb["non_optimizing_components"]["utility"] == pytest.approx(10.0)
    assert cb["non_optimizing_components"]["solar_pv"] == pytest.approx(16.0)
    assert cb["non_optimizing_components"]["battery_energy_storage"] == pytest.approx(8.0)
