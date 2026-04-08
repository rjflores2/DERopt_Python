"""Build-model tests for flow battery (decoupled energy/power capacity)."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model


def _minimal_data(*, n_time: int = 2) -> DataContainer:
    dts = [datetime(2024, 1, 1, i, 0) for i in range(n_time)]
    return DataContainer(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            "electricity_load__n": [1.0] * n_time,
        },
        static={
            "electricity_load_keys": ["electricity_load__n"],
            "time_step_hours": 1.0,
        },
        import_prices_by_node={"electricity_load__n": [0.0] * n_time},
        utility_rate_by_node={"electricity_load__n": None},
    )


def test_flow_battery_block_builds_with_adoption():
    data = _minimal_data()
    m = build_model(
        data,
        technology_parameters={
            "flow_battery_energy_storage": {
                "allow_adoption": True,
                "energy_capital_cost_per_kwh": 300.0,
                "power_capital_cost_per_kw": 150.0,
                "existing_energy_capacity_by_node": {"electricity_load__n": 0.0},
                "existing_power_capacity_by_node": {"electricity_load__n": 0.0},
            }
        },
        financials={},
    )
    fb = m.flow_battery_energy_storage
    assert fb is not None
    assert hasattr(fb, "energy_capacity_adopted")
    assert hasattr(fb, "power_capacity_adopted")
    fb.energy_capacity_adopted["electricity_load__n"].set_value(0.0)
    fb.power_capacity_adopted["electricity_load__n"].set_value(0.0)
    assert pyo.value(fb.total_energy_capacity["electricity_load__n"]) == pytest.approx(0.0)
    assert pyo.value(fb.total_power_capacity["electricity_load__n"]) == pytest.approx(0.0)


def test_flow_battery_charge_limited_by_power_not_energy_crate():
    """Power cap is independent of energy cap (unlike C-rate * kWh)."""
    data = _minimal_data()
    m = build_model(
        data,
        technology_parameters={
            "flow_battery_energy_storage": {
                "allow_adoption": False,
                "existing_energy_capacity_by_node": {"electricity_load__n": 1000.0},
                "existing_power_capacity_by_node": {"electricity_load__n": 50.0},
            }
        },
        financials={},
    )
    fb = m.flow_battery_energy_storage
    assert pyo.value(fb.total_energy_capacity["electricity_load__n"]) == pytest.approx(1000.0)
    assert pyo.value(fb.total_power_capacity["electricity_load__n"]) == pytest.approx(50.0)
