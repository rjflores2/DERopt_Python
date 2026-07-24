"""Scenario-level hydrokinetic reference_kw/reference_swept_area_m2 must be honored.

Regression coverage for a bug found in review: a scenario that loads its own
hydrokinetic_path can normalize that raw-kW series against its OWN reference_kw
(e.g. a different reference device than the base case's). technologies.hydrokinetic.block
used to always de-normalize with the *base* case's reference_kw/reference_swept_area_m2,
silently producing the wrong kWh/m^2 yield for any scenario whose reference_kw differed
from the base case's.
"""

from __future__ import annotations

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model
from technologies.hydrokinetic.inputs import FORMULATION_HYDROKINETIC_LP

_NODE = "electricity_load__x"
_PROFILE = "hydrokinetic_production__site_a"


def _data(*, n_time: int = 2) -> DataContainer:
    dts = [datetime(2024, 1, 1, i, 0) for i in range(n_time)]
    # Base case: raw kW [40, 80] measured on a 100 kW reference device with 50 m^2 swept
    # area -> normalized series (raw_kw / 100) = [0.4, 0.8]. Physically correct yield is
    # raw_kw / area = [0.8, 1.6] kWh/m^2 (dt_hours=1.0).
    base_raw_kw = [40.0, 80.0]
    base_reference_kw = 100.0
    reference_area = 50.0
    base_series = [v / base_reference_kw for v in base_raw_kw]

    # Scenario "dry": a DIFFERENT raw-kW profile [20, 60] measured on a 200 kW reference
    # device with the SAME 50 m^2 swept area -> normalized series (raw_kw / 200) = [0.1, 0.3].
    # Physically correct yield is raw_kw / area = [0.4, 1.2] kWh/m^2 -- half the base case's
    # per-raw-kW yield, by construction, so the two are easy to tell apart.
    dry_raw_kw = [20.0, 60.0]
    dry_reference_kw = 200.0
    dry_series = [v / dry_reference_kw for v in dry_raw_kw]

    return DataContainer(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            _NODE: [0.5] * n_time,
            _PROFILE: base_series,
        },
        static={
            "electricity_load_keys": [_NODE],
            "hydrokinetic_production_keys": [_PROFILE],
            "time_step_hours": 1.0,
            "hydrokinetic_reference_kw": base_reference_kw,
            "hydrokinetic_reference_swept_area_m2": reference_area,
        },
        import_prices_by_node={_NODE: [0.0] * n_time},
        utility_rate_by_node={_NODE: None},
        scenario_keys=["_default", "dry"],
        scenario_probability={"_default": 0.5, "dry": 0.5},
        scenario_timeseries={"dry": {_PROFILE: dry_series}},
        scenario_hydrokinetic_reference_kw={"dry": dry_reference_kw},
        scenario_hydrokinetic_reference_swept_area_m2={"dry": reference_area},
    )


def test_scenario_specific_reference_kw_is_honored_not_base_case_scale():
    data = _data()
    m = build_model(
        data,
        technology_parameters={
            "hydrokinetic": {
                "formulation": FORMULATION_HYDROKINETIC_LP,
                "allow_adoption": True,
                "capital_cost_per_kw": 1000.0,
            }
        },
        financials={},
    )

    base_yield = [
        pyo.value(m.hydrokinetic.yield_kwh_per_m2[_PROFILE, "_default", t]) for t in (0, 1)
    ]
    dry_yield = [
        pyo.value(m.hydrokinetic.yield_kwh_per_m2[_PROFILE, "dry", t]) for t in (0, 1)
    ]

    assert base_yield == pytest.approx([0.8, 1.6])
    # Before the fix this would come out as [0.2, 0.6] (dry's series de-normalized with the
    # BASE case's reference_kw=100 instead of its own reference_kw=200) -- half the correct
    # value, silently wrong.
    assert dry_yield == pytest.approx([0.4, 1.2])


def test_scenario_without_its_own_hydrokinetic_override_uses_base_scale():
    """A scenario that does NOT set its own reference_kw must still use the base scale --
    confirms the fix's dict.get(s, base) fallback doesn't break the common (no-override) case.
    """
    data = _data()
    data.scenario_keys = ["_default", "wet"]
    data.scenario_probability = {"_default": 0.5, "wet": 0.5}
    # "wet" has no scenario_timeseries/scenario_hydrokinetic_reference_kw entry at all.

    m = build_model(
        data,
        technology_parameters={
            "hydrokinetic": {
                "formulation": FORMULATION_HYDROKINETIC_LP,
                "allow_adoption": True,
                "capital_cost_per_kw": 1000.0,
            }
        },
        financials={},
    )

    wet_yield = [pyo.value(m.hydrokinetic.yield_kwh_per_m2[_PROFILE, "wet", t]) for t in (0, 1)]
    assert wet_yield == pytest.approx([0.8, 1.6])
