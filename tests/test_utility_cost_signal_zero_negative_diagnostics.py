"""Focused tests for utility cost-signal diagnostics (warnings only)."""

from __future__ import annotations

from datetime import datetime

import pytest

from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.model_diagnostics import collect_model_diagnostics


def _base_data(*, import_prices, demand_charges=None, customer_fixed_charges=None) -> DataContainer:
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "time_serial": [0],
            "electricity_load__x": [1.0],
            "datetime": [datetime(2026, 1, 1)],
        },
        static={"electricity_load_keys": ["electricity_load__x"]},
        import_prices=import_prices,
    )

    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="Test Utility",
        name="Test Rate",
        demand_charges=demand_charges,
        customer_fixed_charges=customer_fixed_charges,
    )
    return data


def test_fixed_charge_only_triggers_free_grid_warning():
    data = _base_data(
        import_prices=None,
        customer_fixed_charges={
            "first_meter": {"amount": 10.0, "units": "$/day"}
        },
        demand_charges=None,
    )
    model = build_model(data, technology_parameters={}, financials={})
    w = collect_model_diagnostics(model, data, None)
    assert any(
        "Grid imports may be free in the optimization" in x
        for x in w
    )


def test_zero_energy_price_no_demand_charges_triggers_free_grid_warning():
    data = _base_data(import_prices=[0.0], demand_charges=None, customer_fixed_charges=None)
    model = build_model(data, technology_parameters={}, financials={})
    w = collect_model_diagnostics(model, data, None)
    assert any(
        "Grid imports may be free in the optimization" in x
        for x in w
    )


def test_negative_energy_price_triggers_negative_energy_price_warning():
    data = _base_data(import_prices=[-0.02], demand_charges=None, customer_fixed_charges=None)
    model = build_model(data, technology_parameters={}, financials={})
    w = collect_model_diagnostics(model, data, None)
    assert any(
        "negative utility energy prices detected" in x.lower() and "-0.02" in x
        for x in w
    )


def test_zero_demand_charge_rates_triggers_zero_demand_charge_warning():
    data = _base_data(
        import_prices=[0.1],
        customer_fixed_charges=None,
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": [[{"rate": 0.0}]],
            "flat_demand_charge_applicable_months": [0],
            "flat_demand_charge_months": [0] * 12,
        },
    )
    # Demand-charge modeling is time-step dependent.
    data.static["time_step_hours"] = 1.0

    model = build_model(data, technology_parameters={}, financials={})
    w = collect_model_diagnostics(model, data, None)
    assert any("all applicable demand-charge rates are zero" in x.lower() for x in w)


def test_negative_demand_charge_rates_triggers_negative_demand_charge_warning():
    data = _base_data(
        import_prices=[0.1],
        customer_fixed_charges=None,
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": [[{"rate": -5.0}]],
            "flat_demand_charge_applicable_months": [0],
            "flat_demand_charge_months": [0] * 12,
        },
    )
    data.static["time_step_hours"] = 1.0

    model = build_model(data, technology_parameters={}, financials={})
    w = collect_model_diagnostics(model, data, None)
    assert any("negative demand-charge rates detected" in x.lower() and "-5" in x for x in w)

