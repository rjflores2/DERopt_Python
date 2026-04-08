"""Integration: equipment-cost diagnostics after build, and plug-in discovery."""

from types import SimpleNamespace

import utilities.model_diagnostics as model_diagnostics_mod
from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.model_diagnostics import collect_model_diagnostics


def test_iter_technology_diagnostic_collectors_finds_modules():
    fns = list(model_diagnostics_mod._iter_technology_diagnostic_collectors())
    assert len(fns) >= 2


def test_negative_solar_capital_warns_after_successful_build():
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "time_serial": [0],
            "load_k": [1.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["load_k"],
            "solar_production_keys": ["solar_p"],
        },
    )
    tech = {"solar_pv": {"capital_cost_per_kw": -50.0, "om_per_kw_year": 2.0}}
    model = build_model(data, technology_parameters=tech, financials={})
    assert model is not None
    case = SimpleNamespace(technology_parameters=tech)
    w = collect_model_diagnostics(model, data, case)
    assert any("negative" in x.lower() for x in w)


def test_negative_solar_capital_with_marginal_existing_recovery_still_builds():
    """Negative marginal capital must not trip existing-recovery validation before diagnostics."""
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "time_serial": [0],
            "load_k": [1.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["load_k"],
            "solar_production_keys": ["solar_p"],
        },
    )
    tech = {
        "solar_pv": {
            "capital_cost_per_kw": -40.0,
            "om_per_kw_year": 1.0,
            "use_marginal_capital_for_existing_recovery": True,
            "existing_solar_capacity_by_node_and_profile": {("load_k", "solar_p"): 1.0},
        }
    }
    model = build_model(data, technology_parameters=tech, financials={})
    assert model is not None
    w = collect_model_diagnostics(model, data, SimpleNamespace(technology_parameters=tech))
    assert any("negative" in x.lower() for x in w)


def test_negative_flow_battery_energy_capital_warns_after_successful_build():
    data = DataContainer(
        indices={"time": [0]},
        timeseries={"time_serial": [0], "load_k": [1.0]},
        static={"electricity_load_keys": ["load_k"], "time_step_hours": 1.0},
        import_prices_by_node={"load_k": [0.0]},
        utility_rate_by_node={"load_k": None},
    )
    tech = {
        "flow_battery_energy_storage": {
            "energy_capital_cost_per_kwh": -10.0,
            "power_capital_cost_per_kw": 1.0,
            "energy_om_per_kwh_year": 1.0,
            "power_om_per_kw_year": 1.0,
        }
    }
    model = build_model(data, technology_parameters=tech, financials={})
    assert model is not None
    case = SimpleNamespace(technology_parameters=tech)
    w = collect_model_diagnostics(model, data, case)
    assert any("negative" in x.lower() and "flow battery" in x.lower() for x in w)


def test_negative_battery_capital_warns_after_successful_build():
    data = DataContainer(
        indices={"time": [0]},
        timeseries={"time_serial": [0], "load_k": [1.0]},
        static={"electricity_load_keys": ["load_k"]},
    )
    tech = {"battery_energy_storage": {"capital_cost_per_kwh": -10.0, "om_per_kwh_year": 1.0}}
    model = build_model(data, technology_parameters=tech, financials={})
    assert model is not None
    case = SimpleNamespace(technology_parameters=tech)
    w = collect_model_diagnostics(model, data, case)
    assert any("negative" in x.lower() and "battery" in x.lower() for x in w)
