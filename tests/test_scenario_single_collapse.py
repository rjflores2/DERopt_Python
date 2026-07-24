"""Regression: one implicit scenario must reproduce today's deterministic math exactly.

This is the core proof for the two-stage stochastic extension's governing invariant (see
docs/plan): solving with a single scenario at probability 1.0 is a byte-for-byte
reproduction of the pre-stochastic model's cost/balance math, not an approximation. Uses
diesel_generator as the pilot technology block (the first fully scenario-indexed block).
No utility block is attached (no import prices configured), so this exercises only the
already-migrated model/core.py + diesel_generator surface -- not-yet-migrated technologies
(solar_pv, battery_energy_storage, hydrokinetic, the utility block, ...) are out of scope here.
"""

from __future__ import annotations

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model

_NODE = "electricity_load__x"
_LOAD_BY_T = {0: 5.0, 1: 8.0, 2: 3.0}


def _single_node_data() -> DataContainer:
    return DataContainer(
        indices={"time": [0, 1, 2]},
        timeseries={
            "time_serial": [0, 1, 2],
            _NODE: [_LOAD_BY_T[0], _LOAD_BY_T[1], _LOAD_BY_T[2]],
        },
        static={
            "electricity_load_keys": [_NODE],
            "time_step_hours": 1.0,
        },
    )


def _build_diesel_model():
    data = _single_node_data()
    return build_model(
        data,
        technology_parameters={
            "diesel_generator": {
                "formulation": "diesel_lp",
                "existing_capacity_by_node": {_NODE: 20.0},
            }
        },
        financials={},
    )


def test_default_scenario_state_is_single_implicit_scenario():
    """A DataContainer with no scenarios configured keeps the default: one scenario at 1.0."""
    data = _single_node_data()
    assert data.scenario_keys == ["_default"]
    assert data.scenario_probability == {"_default": 1.0}


def test_model_scenarios_set_has_exactly_one_implicit_scenario():
    model = _build_diesel_model()
    assert list(model.SCENARIOS) == ["_default"]
    assert pyo.value(model.scenario_probability["_default"]) == pytest.approx(1.0)
    assert getattr(model, "utility", None) is None  # no import prices configured


def test_diesel_generation_is_scenario_indexed_at_node_scenario_t():
    model = _build_diesel_model()
    for t in model.T:
        # Subscriptable at the new 3-tuple (node, scenario, t) shape; raises if the
        # scenario dimension was dropped or misordered.
        assert model.diesel_generator.diesel_generation[_NODE, "_default", t] is not None


def test_single_scenario_variable_cost_matches_unweighted_deterministic_formula():
    """Core regression: probability-weighted cost with one scenario at weight 1.0 must equal
    the plain (pre-stochastic) sum -- no scaling, no approximation.
    """
    model = _build_diesel_model()
    gen_values = {0: 2.0, 1: 5.0, 2: 1.5}
    for t, v in gen_values.items():
        model.diesel_generator.diesel_generation[_NODE, "_default", t].value = v

    variable_om = pyo.value(model.diesel_generator.variable_om_per_kwh)
    fuel_cost_per_kwh = pyo.value(model.diesel_generator.fuel_cost_per_kwh_diesel)
    electric_efficiency = pyo.value(model.diesel_generator.electric_efficiency)

    expected_om_cost = sum(variable_om * v for v in gen_values.values())
    expected_fuel_cost = sum(
        (fuel_cost_per_kwh / electric_efficiency) * v for v in gen_values.values()
    )

    assert pyo.value(model.diesel_generator.diesel_variable_om_cost) == pytest.approx(expected_om_cost)
    assert pyo.value(model.diesel_generator.diesel_fuel_cost) == pytest.approx(expected_fuel_cost)


def test_electricity_balance_uses_scenario_indexed_load_and_source():
    model = _build_diesel_model()
    for t in model.T:
        load = _LOAD_BY_T[t]
        model.diesel_generator.diesel_generation[_NODE, "_default", t].value = load
        assert pyo.value(model.electricity_load[_NODE, "_default", t]) == pytest.approx(load)
        assert pyo.value(model.electricity_sources[_NODE, "_default", t]) == pytest.approx(load)
        assert pyo.value(model.electricity_sinks[_NODE, "_default", t]) == pytest.approx(load)
        # Balance constraint body is satisfied (sources - sinks == 0) at every timestep.
        body = pyo.value(model.electricity_sources[_NODE, "_default", t]) - pyo.value(
            model.electricity_sinks[_NODE, "_default", t]
        )
        assert body == pytest.approx(0.0)


def test_multi_scenario_unequal_probability_weighted_cost_is_not_a_plain_average():
    """The single-scenario tests above can't distinguish "probability-weighted" from
    "unweighted" since probability is always 1.0 for one scenario. This is the one place
    that actually exercises shared.cost_helpers.time_summed_variable_cost's probability
    argument with >1 scenario and UNEQUAL weights, via a technology block (not just the
    utility block, which test_scenario_utility_override.py already covers) -- found as a
    coverage gap in review: a bug in a probability-weighting sum (wrong index, or omitted
    entirely, i.e. an unweighted plain sum) would pass every other test in this module.
    """
    data = DataContainer(
        indices={"time": [0, 1]},
        timeseries={"time_serial": [0, 1], _NODE: [5.0, 5.0]},
        static={"electricity_load_keys": [_NODE], "time_step_hours": 1.0},
        scenario_keys=["wet", "dry"],
        scenario_probability={"wet": 0.25, "dry": 0.75},
    )
    model = build_model(
        data,
        technology_parameters={
            "diesel_generator": {
                "formulation": "diesel_lp",
                "existing_capacity_by_node": {_NODE: 20.0},
            }
        },
        financials={},
    )
    model.diesel_generator.diesel_generation[_NODE, "wet", 0].value = 2.0
    model.diesel_generator.diesel_generation[_NODE, "wet", 1].value = 4.0
    model.diesel_generator.diesel_generation[_NODE, "dry", 0].value = 10.0
    model.diesel_generator.diesel_generation[_NODE, "dry", 1].value = 12.0

    variable_om = pyo.value(model.diesel_generator.variable_om_per_kwh)
    # expected = 0.25*(2+4)*om + 0.75*(10+12)*om -- a plain average (0.5/0.5) or an unweighted
    # sum would both give a different number, so this catches either failure mode.
    expected_om_cost = variable_om * (0.25 * (2.0 + 4.0) + 0.75 * (10.0 + 12.0))
    assert expected_om_cost != pytest.approx(variable_om * (0.5 * (2.0 + 4.0) + 0.5 * (10.0 + 12.0)))

    assert pyo.value(model.diesel_generator.diesel_variable_om_cost) == pytest.approx(expected_om_cost)


def test_diesel_capacity_is_not_scenario_indexed():
    """Stage-1 invariant: capacity/adoption variables must stay scenario-independent."""
    model = _build_diesel_model()
    # installed_capacity = existing_capacity + diesel_capacity_adopted (an unsolved decision
    # var); fix adoption at 0.0 so the Expression evaluates without a real solve.
    model.diesel_generator.diesel_capacity_adopted[_NODE].value = 0.0
    # installed_capacity is [node]-only; subscripting with a scenario/time tuple must fail.
    assert pyo.value(model.diesel_generator.installed_capacity[_NODE]) == pytest.approx(20.0)
    with pytest.raises((KeyError, TypeError)):
        model.diesel_generator.installed_capacity[_NODE, "_default"]
