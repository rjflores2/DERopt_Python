"""Scenario-level utility tariff overrides actually reach the model.

Regression coverage for a bug found in review: resolve_utility_inputs's block-attachment
gate only checked the base case's import_prices_by_node/utility_rate_by_node, so a base
case with no grid connection (islanded) but scenarios that each configure their own
utility_tariffs would silently never get a utility block at all -- discarding every
scenario's configured grid cost with no error.
"""

from __future__ import annotations

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model

_NODE = "electricity_load__x"


def _islanded_base_data() -> DataContainer:
    """Base case has no import_prices_by_node/utility_rate_by_node at all (islanded)."""
    return DataContainer(
        indices={"time": [0, 1]},
        timeseries={"time_serial": [0, 1], _NODE: [5.0, 5.0]},
        static={"electricity_load_keys": [_NODE], "time_step_hours": 1.0},
        scenario_keys=["wet", "dry"],
        scenario_probability={"wet": 0.5, "dry": 0.5},
        scenario_import_prices_by_node={
            "wet": {_NODE: [0.10, 0.10]},
            "dry": {_NODE: [0.50, 0.50]},
        },
    )


def test_scenario_only_tariffs_still_attach_utility_block_with_islanded_base():
    data = _islanded_base_data()
    assert data.import_prices_by_node is None
    assert data.utility_rate_by_node is None

    m = build_model(data, technology_parameters={}, financials={})

    assert hasattr(m, "utility"), (
        "utility block must be attached when any scenario defines its own tariffs, "
        "even if the base case is islanded"
    )


def test_scenario_only_tariffs_produce_correct_probability_weighted_cost():
    data = _islanded_base_data()
    m = build_model(data, technology_parameters={}, financials={})

    for s, price in (("wet", 0.10), ("dry", 0.50)):
        m.utility.grid_import[_NODE, s, 0].value = 5.0
        m.utility.grid_import[_NODE, s, 1].value = 5.0

    # expected = 0.5*(0.10*5 + 0.10*5) + 0.5*(0.50*5 + 0.50*5) = 0.5*1.0 + 0.5*5.0 = 3.0
    assert pyo.value(m.utility.energy_import_cost) == pytest.approx(3.0)


def test_base_and_scenario_both_islanded_still_omits_utility_block():
    """Sanity check the fix didn't overcorrect: a genuinely islanded case (no tariffs
    anywhere, base or scenario) must still omit the utility block entirely."""
    data = DataContainer(
        indices={"time": [0]},
        timeseries={"time_serial": [0], _NODE: [5.0]},
        static={"electricity_load_keys": [_NODE], "time_step_hours": 1.0},
    )
    assert data.import_prices_by_node is None
    assert data.utility_rate_by_node is None

    m = build_model(data, technology_parameters={}, financials={})

    assert getattr(m, "utility", None) is None
