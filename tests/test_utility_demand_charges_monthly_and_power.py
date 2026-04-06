"""Tests for monthly demand-charge formulation and kW conversion."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model


def _sched_12x24(default: int = 0) -> list[list[int]]:
    return [[default for _h in range(24)] for _m in range(12)]


def test_flat_demand_creates_month_peaks_only_for_months_in_run_and_uses_month_rates():
    # Two periods: Jan and Feb
    dts = [datetime(2024, 1, 1, 0, 0), datetime(2024, 2, 1, 0, 0)]
    data = DataContainer(
        indices={"time": [0, 1]},
        timeseries={
            "datetime": dts,
            "time_serial": [0, 1],
            "electricity_load__x": [1.0, 1.0],
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )
    # URDB-like mapping: flatdemandmonths maps month -> structure index (e.g. winter=0, summer=1).
    # Here Jan uses structure 0 (rate=10) and Feb uses structure 1 (rate=20).
    flat_struct = [
        [[{"rate": 10.0}]],  # struct 0
        [[{"rate": 20.0}]],  # struct 1
    ]
    flat_month_map = [0] * 12
    flat_month_map[1] = 1
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": flat_struct,
            "flat_demand_charge_months": flat_month_map,
            "flat_demand_charge_applicable_months": list(range(12)),
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}

    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m, "utility")
    assert hasattr(m.utility, "P_flat_y2024_m0")
    assert hasattr(m.utility, "P_flat_y2024_m1")
    assert not hasattr(m.utility, "P_flat_y2024_m2")

    # Set imports so Jan peak=5 kW and Feb peak=7 kW
    m.utility.grid_import["electricity_load__x", 0].value = 5.0  # kWh in 1 hour => 5 kW
    m.utility.grid_import["electricity_load__x", 1].value = 7.0  # => 7 kW
    m.utility.P_flat_y2024_m0["electricity_load__x"].value = 5.0
    m.utility.P_flat_y2024_m1["electricity_load__x"].value = 7.0

    assert pyo.value(m.utility.nonTOU_Demand_Charge_Cost) == pytest.approx(10.0 * 5.0 + 20.0 * 7.0)


def test_flat_demand_multiyear_creates_separate_year_month_peaks():
    # Two periods: Jan 2024 and Jan 2025 should create two separate peak vars.
    dts = [datetime(2024, 1, 1, 0, 0), datetime(2025, 1, 1, 0, 0)]
    data = DataContainer(
        indices={"time": [0, 1]},
        timeseries={
            "datetime": dts,
            "time_serial": [0, 1],
            "electricity_load__x": [1.0, 1.0],
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )
    flat_struct = [[[{"rate": 10.0}]]]  # one structure, rate=10 for month-of-year index 0
    flat_month_map = [0] * 12
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": flat_struct,
            "flat_demand_charge_months": flat_month_map,
            "flat_demand_charge_applicable_months": list(range(12)),
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}

    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m.utility, "P_flat_y2024_m0")
    assert hasattr(m.utility, "P_flat_y2025_m0")

    # Set imports so the peaks differ by year.
    m.utility.grid_import["electricity_load__x", 0].value = 5.0
    m.utility.grid_import["electricity_load__x", 1].value = 7.0
    m.utility.P_flat_y2024_m0["electricity_load__x"].value = 5.0
    m.utility.P_flat_y2025_m0["electricity_load__x"].value = 7.0

    assert pyo.value(m.utility.nonTOU_Demand_Charge_Cost) == pytest.approx(10.0 * 5.0 + 10.0 * 7.0)


def test_tou_demand_creates_month_tier_peaks_only_when_tier_occurs_in_month():
    # 4 periods, all weekdays at hour=12: two in Jan, two in Feb
    dts = [
        datetime(2024, 1, 2, 12, 0),
        datetime(2024, 1, 3, 12, 0),
        datetime(2024, 2, 6, 12, 0),
        datetime(2024, 2, 7, 12, 0),
    ]
    data = DataContainer(
        indices={"time": list(range(len(dts)))},
        timeseries={
            "datetime": dts,
            "time_serial": list(range(len(dts))),
            "electricity_load__x": [1.0] * len(dts),
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )

    wd = _sched_12x24(0)
    we = _sched_12x24(0)
    # Jan hour 12 -> tier 1; Feb hour 12 -> tier 2
    wd[0][12] = 1
    wd[1][12] = 2
    we[0][12] = 1
    we[1][12] = 2
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "tou",
            "demand_charge_ratestructure": [
                [{"rate": 0.0}],
                [{"rate": 4.0}],
                [{"rate": 9.0}],
            ],
            "demand_charge_weekdayschedule": wd,
            "demand_charge_weekendschedule": we,
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}

    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m, "utility")
    assert hasattr(m.utility, "P_tou_y2024_m0_tier1")
    assert hasattr(m.utility, "P_tou_y2024_m1_tier2")
    assert not hasattr(m.utility, "P_tou_y2024_m0_tier2")
    assert not hasattr(m.utility, "P_tou_y2024_m1_tier1")


def test_tou_demand_charge_cost_sums_month_tier_peaks():
    dts = [
        datetime(2024, 1, 2, 12, 0),
        datetime(2024, 2, 6, 12, 0),
    ]
    data = DataContainer(
        indices={"time": list(range(len(dts)))},
        timeseries={
            "datetime": dts,
            "time_serial": list(range(len(dts))),
            "electricity_load__x": [1.0] * len(dts),
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )
    wd = _sched_12x24(0)
    we = _sched_12x24(0)
    wd[0][12] = 1
    wd[1][12] = 2
    we[0][12] = 1
    we[1][12] = 2
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "tou",
            "demand_charge_ratestructure": [
                [{"rate": 0.0}],
                [{"rate": 4.0}],
                [{"rate": 9.0}],
            ],
            "demand_charge_weekdayschedule": wd,
            "demand_charge_weekendschedule": we,
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}
    m = build_model(data, technology_parameters={}, financials={})
    # Set peak variables directly; cost expression should be sum(rate * P) over created (month,tier) vars.
    m.utility.P_tou_y2024_m0_tier1["electricity_load__x"].value = 5.0
    m.utility.P_tou_y2024_m1_tier2["electricity_load__x"].value = 7.0
    assert pyo.value(m.utility.TOU_Demand_Charge_Cost) == pytest.approx(4.0 * 5.0 + 9.0 * 7.0)


def test_tou_demand_multiyear_creates_separate_year_month_peaks():
    # Two periods: both in Jan (hour 12). Should create two separate peak vars, one per year-month occurrence.
    dts = [datetime(2024, 1, 2, 12, 0), datetime(2025, 1, 3, 12, 0)]
    data = DataContainer(
        indices={"time": list(range(len(dts)))},
        timeseries={
            "datetime": dts,
            "time_serial": list(range(len(dts))),
            "electricity_load__x": [1.0] * len(dts),
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )

    wd = _sched_12x24(0)
    we = _sched_12x24(0)
    # With a single tier in the rate structure, tier assignment is forced to 0.
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "tou",
            "demand_charge_ratestructure": [[{"rate": 4.0}]],
            "demand_charge_weekdayschedule": wd,
            "demand_charge_weekendschedule": we,
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}

    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m.utility, "P_tou_y2024_m0_tier0")
    assert hasattr(m.utility, "P_tou_y2025_m0_tier0")

    m.utility.P_tou_y2024_m0_tier0["electricity_load__x"].value = 5.0
    m.utility.P_tou_y2025_m0_tier0["electricity_load__x"].value = 7.0
    assert pyo.value(m.utility.TOU_Demand_Charge_Cost) == pytest.approx(4.0 * 5.0 + 4.0 * 7.0)


def test_subhourly_demand_charge_uses_power_conversion():
    # One 15-minute period in Jan, grid_import=25 kWh => 100 kW demand proxy
    dts = [datetime(2024, 1, 2, 0, 0)]
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": dts,
            "time_serial": [0],
            "electricity_load__x": [1.0],
        },
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 0.25},
    )
    data.utility_rate = ParsedRate(
        rate_type="tou",
        utility="X",
        name="Y",
        demand_charges={
            "demand_charge_type": "flat",
            "flat_demand_charge_structure": [[{"rate": 1.0}]],
            "flat_demand_charge_applicable_months": [0],
        },
    )
    data.utility_rate_by_node = {"electricity_load__x": data.utility_rate}
    m = build_model(data, technology_parameters={}, financials={})
    m.utility.grid_import["electricity_load__x", 0].value = 25.0
    m.utility.P_flat_y2024_m0["electricity_load__x"].value = 0.0
    power = pyo.value(m.utility.grid_import_power_kw["electricity_load__x", 0])
    assert power == pytest.approx(100.0)

