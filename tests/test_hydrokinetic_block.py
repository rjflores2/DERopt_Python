"""Build-model tests for hydrokinetic LP and unit MILP blocks."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model
from technologies.hydrokinetic.inputs import (
    FORMULATION_HYDROKINETIC_LP,
    FORMULATION_HYDROKINETIC_UNIT_MILP,
)


def _data_with_hkt(*, n_time: int = 2) -> DataContainer:
    dt0 = datetime(2024, 1, 1, 0, 0)
    dt1 = datetime(2024, 1, 1, 1, 0)
    dts = [dt0, dt1][:n_time]
    pot = [1.0] * n_time
    return DataContainer(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            "electricity_load__x": [0.5] * n_time,
            "hydrokinetic_production__site_a": pot,
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "hydrokinetic_production_keys": ["hydrokinetic_production__site_a"],
            "time_step_hours": 1.0,
            "hydrokinetic_reference_kw": 80.0,
            "hydrokinetic_reference_swept_area_m2": 18.0,
        },
        import_prices_by_node={"electricity_load__x": [0.0] * n_time},
        utility_rate_by_node={"electricity_load__x": None},
    )


def test_hydrokinetic_lp_block_builds():
    data = _data_with_hkt()
    m = build_model(
        data,
        technology_parameters={
            "hydrokinetic": {
                "formulation": FORMULATION_HYDROKINETIC_LP,
                "allow_adoption": True,
                "capital_cost_per_kw": 1000.0,
                "max_swept_area_m2_by_node_and_profile": {
                    ("electricity_load__x", "hydrokinetic_production__site_a"): 100.0,
                },
            }
        },
        financials={},
    )
    assert hasattr(m, "hydrokinetic")
    y00 = pyo.value(m.hydrokinetic.yield_kwh_per_m2["hydrokinetic_production__site_a", 0])
    assert y00 == pytest.approx(80.0 / 18.0)


def test_hydrokinetic_unit_milp_block_builds():
    data = _data_with_hkt()
    m = build_model(
        data,
        technology_parameters={
            "hydrokinetic": {
                "formulation": FORMULATION_HYDROKINETIC_UNIT_MILP,
                "allow_adoption": True,
                "unit_swept_area_m2": 18.0,
                "unit_capacity_kw": 80.0,
                "capital_cost_per_kw": 2000.0,
                "max_installed_units_by_node_and_profile": {
                    ("electricity_load__x", "hydrokinetic_production__site_a"): 5,
                },
            }
        },
        financials={},
    )
    assert hasattr(m, "hydrokinetic")
    ua = m.hydrokinetic.units_adopted["electricity_load__x", "hydrokinetic_production__site_a"]
    assert ua.domain is pyo.NonNegativeIntegers


def test_hydrokinetic_requires_reference_swept_area():
    data = _data_with_hkt()
    del data.static["hydrokinetic_reference_swept_area_m2"]
    with pytest.raises(ValueError, match="hydrokinetic_reference_swept_area_m2"):
        build_model(
            data,
            technology_parameters={"hydrokinetic": {"formulation": FORMULATION_HYDROKINETIC_LP}},
            financials={},
        )
