"""Build/solve tests for the controllable_hydro block (conventional dam + pumped storage).

Fixture style follows ``tests/test_hydrokinetic_block.py`` (DataContainer + build_model) and
``tests/test_technology_block_contract.py`` (hand-built minimal model + direct register()
calls for contract/guard checks that don't need a full solve).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.contracts import validate_technology_block_interface
from model.core import build_model
from technologies.controllable_hydro import register
from technologies.controllable_hydro.inputs import (
    BOUNDARY_CYCLIC,
    BOUNDARY_FIXED_ENDPOINTS,
    BOUNDARY_FREE_END,
    resolve_controllable_hydro_block_inputs,
)

NODE = "electricity_load__x"


def _gurobi_available() -> bool:
    try:
        return bool(pyo.SolverFactory("gurobi").available())
    except Exception:
        return False


requires_solver = pytest.mark.skipif(not _gurobi_available(), reason="Gurobi is not available")


def _solve(m: pyo.ConcreteModel):
    solver = pyo.SolverFactory("gurobi")
    return solver.solve(m)


def _base_data(
    *,
    n_time: int,
    unit_inflow: dict[str, list[float]],
    load: list[float] | None = None,
    time_step_hours: float = 1.0,
    import_prices: list[float] | None = None,
    scenario_keys: list[str] | None = None,
    scenario_probability: dict[str, float] | None = None,
    scenario_unit_inflow: dict[str, dict[str, list[float]]] | None = None,
) -> DataContainer:
    dts = [datetime(2024, 1, 1, 0, 0) + timedelta(hours=i) for i in range(n_time)]
    load = load if load is not None else [0.0] * n_time
    timeseries: dict[str, Any] = {
        "datetime": dts,
        "time_serial": [0.0] * n_time,
        NODE: load,
    }
    unit_keys = list(unit_inflow.keys())
    inflow_keys: dict[str, str] = {}
    for unit, series in unit_inflow.items():
        key = f"controllable_hydro_inflow__{unit}"
        timeseries[key] = series
        inflow_keys[unit] = key

    kwargs: dict[str, Any] = dict(
        indices={"time": list(range(n_time))},
        timeseries=timeseries,
        static={
            "electricity_load_keys": [NODE],
            "controllable_hydro_unit_keys": unit_keys,
            "controllable_hydro_inflow_keys": inflow_keys,
            "time_step_hours": time_step_hours,
        },
    )
    if import_prices is not None:
        kwargs["import_prices_by_node"] = {NODE: import_prices}
        kwargs["utility_rate_by_node"] = {NODE: None}
    if scenario_keys is not None:
        kwargs["scenario_keys"] = scenario_keys
    if scenario_probability is not None:
        kwargs["scenario_probability"] = scenario_probability
    if scenario_unit_inflow is not None:
        scen_ts: dict[str, dict[str, list[float]]] = {}
        for s, per_unit in scenario_unit_inflow.items():
            scen_ts[s] = {inflow_keys[u]: series for u, series in per_unit.items()}
        kwargs["scenario_timeseries"] = scen_ts

    return DataContainer(**kwargs)


def _minimal_model(
    *,
    n_time: int = 2,
    scenario_keys: list[str] | None = None,
    scenario_probability: dict[str, float] | None = None,
) -> pyo.ConcreteModel:
    scenario_keys = scenario_keys or ["_default"]
    if scenario_probability is None:
        p = 1.0 / len(scenario_keys)
        scenario_probability = {s: p for s in scenario_keys}
    m = pyo.ConcreteModel()
    m.T = pyo.Set(initialize=list(range(n_time)), ordered=True)
    m.NODES = pyo.Set(initialize=[NODE], ordered=True)
    m.SCENARIOS = pyo.Set(initialize=scenario_keys, ordered=True)
    m.scenario_probability = pyo.Param(
        m.SCENARIOS, initialize=scenario_probability, within=pyo.PercentFraction, mutable=False
    )
    m.time_step_hours = pyo.Param(initialize=1.0, within=pyo.PositiveReals, mutable=False)
    return m


# --------------------------------------------------------------------------------------
# 1. Contract
# --------------------------------------------------------------------------------------


def test_contract_interface_and_register_identity():
    data = _base_data(n_time=2, unit_inflow={"u1": [10.0, 10.0]})
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
    }
    m = _minimal_model(n_time=2)
    returned = register(
        m, data, technology_parameters={"controllable_hydro": params}, financials={}
    )
    assert returned is m.controllable_hydro
    validate_technology_block_interface(
        technology_key="controllable_hydro", block=m.controllable_hydro, model=m
    )


# --------------------------------------------------------------------------------------
# 2. Registry guard
# --------------------------------------------------------------------------------------


def test_register_returns_none_without_unit_keys():
    data = _base_data(n_time=2, unit_inflow={"u1": [10.0, 10.0]})
    del data.static["controllable_hydro_unit_keys"]
    m = _minimal_model(n_time=2)
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
    }
    result = register(
        m, data, technology_parameters={"controllable_hydro": params}, financials={}
    )
    assert result is None
    assert not hasattr(m, "controllable_hydro")


def test_register_returns_none_when_params_none():
    data = _base_data(n_time=2, unit_inflow={"u1": [10.0, 10.0]})
    m = _minimal_model(n_time=2)
    result = register(
        m, data, technology_parameters={"controllable_hydro": None}, financials={}
    )
    assert result is None
    assert not hasattr(m, "controllable_hydro")


# --------------------------------------------------------------------------------------
# 3. Inventory conservation (forced dispatch, free_end)
# --------------------------------------------------------------------------------------


@requires_solver
def test_inventory_conservation_forced_dispatch():
    n_time = 3
    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [100.0, 100.0, 100.0]},
        load=[45.0, 45.0, 45.0],
    )
    params = {
        "boundary_condition": BOUNDARY_FREE_END,
        "turbine_efficiency": 0.9,
        "start_soc_fraction": 0.5,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 1000.0},
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    hydro = m.controllable_hydro
    s = "_default"
    for t in m.T:
        hydro.turbine_generation[NODE, "u1", s, t].fix(45.0)
        hydro.pump_consumption[NODE, "u1", s, t].fix(0.0)
        hydro.spill[NODE, "u1", s, t].fix(0.0)

    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    inv = [pyo.value(hydro.reservoir_inventory[NODE, "u1", s, t]) for t in m.T]
    # E0 = 500 (anchor); each period nets inflow(100) - gen/eta(45/0.9=50) = +50.
    expected = [500.0, 550.0, 600.0]
    for got, exp in zip(inv, expected):
        assert got == pytest.approx(exp, abs=1e-6)


# --------------------------------------------------------------------------------------
# 4. Cyclic closure
# --------------------------------------------------------------------------------------


@requires_solver
def test_cyclic_closure():
    n_time = 4
    inflow = [50.0, 30.0, 0.0, 0.0]
    load = [0.0, 0.0, 20.0, 60.0]
    data = _base_data(n_time=n_time, unit_inflow={"u1": inflow}, load=load)
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 1.0,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    hydro = m.controllable_hydro
    s = "_default"
    horizon = list(m.T)

    # Generation forced to match load exactly (no utility block); net changes per period
    # are d = [50, 30, -20, -60], summing to zero over the cycle.
    for t, g in zip(horizon, load):
        hydro.turbine_generation[NODE, "u1", s, t].fix(g)
        hydro.pump_consumption[NODE, "u1", s, t].fix(0.0)
        hydro.spill[NODE, "u1", s, t].fix(0.0)

    # Anchor the first period directly to remove the cyclic system's free additive
    # constant, then let the remaining three periods be solved via forward substitution.
    hydro.reservoir_inventory[NODE, "u1", s, horizon[0]].fix(500.0)

    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    inv = [pyo.value(hydro.reservoir_inventory[NODE, "u1", s, t]) for t in horizon]
    expected = [500.0, 530.0, 510.0, 450.0]
    for got, exp in zip(inv, expected):
        assert got == pytest.approx(exp, abs=1e-6)

    # Explicit wraparound check: inventory at the first timestep equals the value implied
    # by the last timestep and the first-timestep's own flows (retention=1.0 here).
    d0 = inflow[0] - load[0]
    assert inv[0] == pytest.approx(inv[-1] + d0, abs=1e-6)


# --------------------------------------------------------------------------------------
# 5 & 6. Spill activates under high inflow; infeasible with spill fixed to zero.
# --------------------------------------------------------------------------------------


def _wet_period_data_and_params(n_time: int = 2):
    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [1000.0] * n_time},
        load=[0.0] * n_time,
    )
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 50.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 10.0},
    }
    return data, params


@requires_solver
def test_spill_activates_under_high_inflow():
    data, params = _wet_period_data_and_params()
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    hydro = m.controllable_hydro
    s = "_default"
    total_spill = sum(pyo.value(hydro.spill[NODE, "u1", s, t]) for t in m.T)
    assert total_spill > 0.0


@requires_solver
def test_infeasible_when_spill_fixed_to_zero_under_high_inflow():
    data, params = _wet_period_data_and_params()
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    hydro = m.controllable_hydro
    s = "_default"
    for t in m.T:
        hydro.spill[NODE, "u1", s, t].fix(0.0)

    result = _solve(m)
    assert result.solver.termination_condition in (
        pyo.TerminationCondition.infeasible,
        pyo.TerminationCondition.infeasibleOrUnbounded,
    )


# --------------------------------------------------------------------------------------
# 7. Minimum release binds (satisfied via spill, not unwanted generation).
# --------------------------------------------------------------------------------------


@requires_solver
def test_minimum_release_binds_using_spill():
    n_time = 2
    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [100.0, 100.0]},
        load=[0.0, 0.0],
    )
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 1000.0},
        "minimum_release_kwh_by_unit_time": {("u1", 0): 50.0},
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    hydro = m.controllable_hydro
    s = "_default"
    gen0 = pyo.value(hydro.turbine_generation[NODE, "u1", s, 0])
    spill0 = pyo.value(hydro.spill[NODE, "u1", s, 0])
    eta = pyo.value(hydro.turbine_efficiency)

    # No demand anywhere in the model: generation stays at zero (there's no outlet for
    # electricity), so the minimum release is met entirely via spill.
    for t in m.T:
        assert pyo.value(hydro.turbine_generation[NODE, "u1", s, t]) == pytest.approx(0.0, abs=1e-6)
    assert gen0 / eta + spill0 >= 50.0 - 1e-6
    assert spill0 >= 50.0 - 1e-6


# --------------------------------------------------------------------------------------
# 8. Conventional dam (zero pump capacity).
# --------------------------------------------------------------------------------------


@requires_solver
def test_conventional_dam_zero_pump_consumption():
    n_time = 2
    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [200.0, 200.0]},
        load=[50.0, 50.0],
    )
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 200.0},
        # pump_capacity_kw_by_node_unit omitted -> defaults to 0.0 (conventional dam).
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    hydro = m.controllable_hydro
    s = "_default"
    for t in m.T:
        assert pyo.value(hydro.pump_consumption[NODE, "u1", s, t]) == pytest.approx(0.0, abs=1e-9)
        assert pyo.value(hydro.electricity_sink_term[NODE, s, t]) == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------------------
# 9. Pumped storage loses energy (round trip = product of the two efficiencies).
# --------------------------------------------------------------------------------------


@requires_solver
def test_pumped_storage_round_trip_loses_energy():
    n_time = 3
    pump_in = 100.0
    eta_pump = 0.9
    eta_gen = 0.85
    round_trip_out = eta_pump * eta_gen * pump_in  # 76.5, must be < pump_in

    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [0.0, 0.0, 0.0]},
        load=[0.0, 0.0, round_trip_out],
        import_prices=[0.0, 0.0, 0.0],
    )
    params = {
        "boundary_condition": BOUNDARY_FIXED_ENDPOINTS,
        "turbine_efficiency": eta_gen,
        "pump_efficiency": eta_pump,
        "start_soc_fraction": 0.5,
        "end_soc_fraction": 0.5,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
        "pump_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    hydro = m.controllable_hydro
    s = "_default"
    horizon = list(m.T)
    t0, t1, t2 = horizon

    # t0: inert anchor period.
    hydro.turbine_generation[NODE, "u1", s, t0].fix(0.0)
    hydro.pump_consumption[NODE, "u1", s, t0].fix(0.0)
    hydro.spill[NODE, "u1", s, t0].fix(0.0)
    # t1: pump leg only.
    hydro.turbine_generation[NODE, "u1", s, t1].fix(0.0)
    hydro.pump_consumption[NODE, "u1", s, t1].fix(pump_in)
    hydro.spill[NODE, "u1", s, t1].fix(0.0)
    # t2: turbine leg only (generation left free -- this is what we're checking).
    hydro.pump_consumption[NODE, "u1", s, t2].fix(0.0)
    hydro.spill[NODE, "u1", s, t2].fix(0.0)

    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    gen2 = pyo.value(hydro.turbine_generation[NODE, "u1", s, t2])
    assert gen2 == pytest.approx(round_trip_out, abs=1e-6)
    assert gen2 < pump_in


# --------------------------------------------------------------------------------------
# 10. Boundary conditions differ: free_end cost no higher than cyclic.
# --------------------------------------------------------------------------------------


def _boundary_comparison_data(n_time: int = 4):
    return _base_data(
        n_time=n_time,
        unit_inflow={"u1": [50.0] * n_time},
        load=[100.0] * n_time,
        import_prices=[1.0] * n_time,
    )


@requires_solver
def test_boundary_conditions_differ_free_end_cost_no_higher_than_cyclic():
    n_time = 4
    base_params = {
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 500.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
        "variable_om_per_kwh": 0.05,
    }

    data_cyclic = _boundary_comparison_data(n_time)
    params_cyclic = {**base_params, "boundary_condition": BOUNDARY_CYCLIC}
    m_cyclic = build_model(
        data_cyclic, technology_parameters={"controllable_hydro": params_cyclic}, financials={}
    )
    result_cyclic = _solve(m_cyclic)
    assert result_cyclic.solver.termination_condition == pyo.TerminationCondition.optimal
    cost_cyclic = pyo.value(m_cyclic.obj)

    data_free_end = _boundary_comparison_data(n_time)
    params_free_end = {
        **base_params,
        "boundary_condition": BOUNDARY_FREE_END,
        "start_soc_fraction": 0.5,
    }
    m_free_end = build_model(
        data_free_end, technology_parameters={"controllable_hydro": params_free_end}, financials={}
    )
    result_free_end = _solve(m_free_end)
    assert result_free_end.solver.termination_condition == pyo.TerminationCondition.optimal
    cost_free_end = pyo.value(m_free_end.obj)

    assert cost_free_end <= cost_cyclic + 1e-6


# --------------------------------------------------------------------------------------
# 11. Sub-hourly timestep: capacity limits scale, energy result matches an hourly case.
# --------------------------------------------------------------------------------------


@requires_solver
def test_sub_hourly_capacity_limit_scales():
    # Part A: turbine_limit = turbine_capacity_kw * time_step_hours. With capacity 100 kW
    # and dt=0.25h, the limit is exactly 25 kWh/timestep -- forced via load-matching (no
    # other source), so it is feasible at 25.0 and infeasible just above it.
    def _one_period_model(load_value: float, time_step_hours: float) -> pyo.ConcreteModel:
        data = _base_data(
            n_time=1,
            unit_inflow={"u1": [1000.0]},
            load=[load_value],
            time_step_hours=time_step_hours,
        )
        params = {
            "boundary_condition": BOUNDARY_CYCLIC,
            "turbine_efficiency": 0.9,
            "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 10_000.0},
            "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
        }
        return build_model(data, technology_parameters={"controllable_hydro": params}, financials={})

    m_ok = _one_period_model(25.0, 0.25)
    result_ok = _solve(m_ok)
    assert result_ok.solver.termination_condition == pyo.TerminationCondition.optimal

    m_bad = _one_period_model(25.1, 0.25)
    result_bad = _solve(m_bad)
    assert result_bad.solver.termination_condition in (
        pyo.TerminationCondition.infeasible,
        pyo.TerminationCondition.infeasibleOrUnbounded,
    )


@requires_solver
def test_sub_hourly_energy_result_matches_hourly_case():
    # Part B: an inert first period (zero inflow, zero load) anchors the free_end starting
    # level identically in both models, so period-1-onward dynamics are directly comparable
    # regardless of how finely the same two physical hours are discretized.
    eta = 0.9
    reservoir_capacity = 1000.0
    turbine_capacity = 1000.0
    start_soc_fraction = 0.5

    def _params():
        return {
            "boundary_condition": BOUNDARY_FREE_END,
            "turbine_efficiency": eta,
            "start_soc_fraction": start_soc_fraction,
            "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): reservoir_capacity},
            "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): turbine_capacity},
        }

    # Hourly: t0 inert, t1 = hour A (inflow 100, gen 45), t2 = hour B (inflow 0, gen 45).
    hourly_data = _base_data(
        n_time=3,
        unit_inflow={"u1": [0.0, 100.0, 0.0]},
        load=[0.0, 45.0, 45.0],
        time_step_hours=1.0,
    )
    m_hourly = build_model(
        hourly_data, technology_parameters={"controllable_hydro": _params()}, financials={}
    )
    hydro_hourly = m_hourly.controllable_hydro
    s = "_default"
    for t in m_hourly.T:
        hydro_hourly.spill[NODE, "u1", s, t].fix(0.0)
    result_hourly = _solve(m_hourly)
    assert result_hourly.solver.termination_condition == pyo.TerminationCondition.optimal

    end_of_hour_a = pyo.value(hydro_hourly.reservoir_inventory[NODE, "u1", s, 1])
    end_of_hour_b = pyo.value(hydro_hourly.reservoir_inventory[NODE, "u1", s, 2])

    # Sub-hourly: t0 inert, t1..t4 = hour A split into 4 quarter-hours, t5..t8 = hour B.
    sub_inflow = [0.0] + [25.0] * 4 + [0.0] * 4
    sub_load = [0.0] + [11.25] * 4 + [11.25] * 4
    sub_data = _base_data(
        n_time=9,
        unit_inflow={"u1": sub_inflow},
        load=sub_load,
        time_step_hours=0.25,
    )
    m_sub = build_model(
        sub_data, technology_parameters={"controllable_hydro": _params()}, financials={}
    )
    hydro_sub = m_sub.controllable_hydro
    for t in m_sub.T:
        hydro_sub.spill[NODE, "u1", s, t].fix(0.0)
    result_sub = _solve(m_sub)
    assert result_sub.solver.termination_condition == pyo.TerminationCondition.optimal

    sub_end_of_hour_a = pyo.value(hydro_sub.reservoir_inventory[NODE, "u1", s, 4])
    sub_end_of_hour_b = pyo.value(hydro_sub.reservoir_inventory[NODE, "u1", s, 8])

    assert sub_end_of_hour_a == pytest.approx(end_of_hour_a, abs=1e-6)
    assert sub_end_of_hour_b == pytest.approx(end_of_hour_b, abs=1e-6)


# --------------------------------------------------------------------------------------
# 12. Scenario handling: shared capacity, per-scenario dispatch, probability-weighted cost.
# --------------------------------------------------------------------------------------


@requires_solver
def test_scenario_handling_shared_capacity_varying_dispatch():
    n_time = 2
    data = _base_data(
        n_time=n_time,
        unit_inflow={"u1": [20.0, 20.0]},  # base series (overridden by both scenarios below)
        load=[100.0, 100.0],
        import_prices=[1.0, 1.0],
        scenario_keys=["wet", "dry"],
        scenario_probability={"wet": 0.7, "dry": 0.3},
        scenario_unit_inflow={
            "wet": {"u1": [200.0, 200.0]},
            "dry": {"u1": [20.0, 20.0]},
        },
    )
    params = {
        "boundary_condition": BOUNDARY_CYCLIC,
        "turbine_efficiency": 0.9,
        "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
        "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 200.0},
        "variable_om_per_kwh": 0.01,
    }
    m = build_model(data, technology_parameters={"controllable_hydro": params}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    hydro = m.controllable_hydro

    # Capacity parameters are indexed only by (node, unit) -- not by scenario.
    assert len(next(iter(hydro.reservoir_capacity_kwh))) == 2
    assert len(next(iter(hydro.turbine_capacity_kw))) == 2

    gen_wet = [pyo.value(hydro.turbine_generation[NODE, "u1", "wet", t]) for t in m.T]
    gen_dry = [pyo.value(hydro.turbine_generation[NODE, "u1", "dry", t]) for t in m.T]
    assert gen_wet != pytest.approx(gen_dry, abs=1e-6)

    # Check the hydro block's own scoped cost contribution (not the whole model's
    # objective, which also includes the utility block's grid-import cost).
    expected_variable_cost = sum(
        pyo.value(m.scenario_probability[s])
        * 0.01
        * pyo.value(hydro.turbine_generation[NODE, "u1", s, t])
        for s in m.SCENARIOS
        for t in m.T
    )
    assert pyo.value(hydro.variable_operating_cost) == pytest.approx(expected_variable_cost, rel=1e-6)
    assert pyo.value(hydro.objective_contribution) == pytest.approx(expected_variable_cost, rel=1e-6)


# --------------------------------------------------------------------------------------
# 13. Input validation: out-of-window start_soc_fraction raises at resolution time.
# --------------------------------------------------------------------------------------


def test_out_of_window_start_soc_fraction_raises_at_resolution_time():
    with pytest.raises(ValueError, match="start_soc_fraction"):
        resolve_controllable_hydro_block_inputs(
            {
                "boundary_condition": BOUNDARY_FREE_END,
                "turbine_efficiency": 0.9,
                "minimum_soc_fraction": 0.1,
                "maximum_soc_fraction": 0.9,
                "start_soc_fraction": 0.05,
                "reservoir_capacity_kwh_by_node_unit": {(NODE, "u1"): 1000.0},
                "turbine_capacity_kw_by_node_unit": {(NODE, "u1"): 100.0},
            },
            nodes=[NODE],
            units=["u1"],
            time_indices=[0, 1],
        )
