"""Wind turbine block construction tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from data_loading.schemas import DataContainer
from model.core import build_model


def test_build_model_constructs_wind_turbine_block():
    data = DataContainer(
        indices={"time": [0, 1]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 1, 0)],
            "time_serial": [0, 1],
            "electricity_load__x": [10.0, 10.0],
            "wind_production__base": [0.25, 0.35],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "wind_production_keys": ["wind_production__base"],
            "time_step_hours": 1.0,
        },
    )
    m = build_model(
        data,
        technology_parameters={"wind_turbine": {}},
        financials={},
    )
    assert hasattr(m, "wind_turbine")
    assert m.wind_turbine.is_constructed()


def test_wind_requested_without_resource_raises():
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "time_step_hours": 1.0,
        },
    )
    with pytest.raises(ValueError, match="was requested in technology_parameters"):
        build_model(data, technology_parameters={"wind_turbine": {}}, financials={})
