"""Demand-charge modeling requires explicit time_step_hours."""

from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model


def test_demand_charges_missing_time_step_hours_raises():
    data = DataContainer(
        indices={"time": [0]},
        timeseries={"time_serial": [0], "electricity_load__x": [1.0], "datetime": []},
        static={"electricity_load_keys": ["electricity_load__x"]},
    )
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="Test Utility",
        name="Test Rate",
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": [[{"rate": 10.0}]],
            "flat_demand_charge_applicable_months": [0],
            "flat_demand_charge_months": [1] * 12,
        },
    )

    try:
        build_model(data, technology_parameters={}, financials={})
        assert False, "Expected ValueError due to missing time_step_hours"
    except ValueError as e:
        assert "time_step_hours" in str(e)

