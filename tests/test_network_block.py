"""Build/solve tests for the network (LinDistFlow) block.

Fixture style follows ``tests/test_hydrokinetic_block.py`` (hand-built ``DataContainer`` +
``build_model``). No technology blocks are used -- with no other source, the electricity
balance forces ``grid_import[node,s,t] == load[node,s,t]`` exactly, which is what makes the
hand-computed voltage/branch-flow checks below possible without needing to solve a real
dispatch optimization.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.network.inputs import NetworkBranch, NetworkTopology

NODE_A = "electricity_load__a"
NODE_B = "electricity_load__b"


def _gurobi_available() -> bool:
    try:
        return bool(pyo.SolverFactory("gurobi").available())
    except Exception:
        return False


requires_solver = pytest.mark.skipif(not _gurobi_available(), reason="Gurobi is not available")


def _solve(m: pyo.ConcreteModel):
    return pyo.SolverFactory("gurobi").solve(m)


def _feeder_topology(**overrides) -> NetworkTopology:
    """Slack S -> A -> B, r=(1.0, 2.0) ohm, x=(0.5, 1.0) ohm, base_kv=12.47 -- same feeder
    hand-verified in tests/test_network_inputs.py."""
    kwargs: dict[str, Any] = dict(
        buses=["S", "A", "B"],
        slack_bus="S",
        branches=[
            NetworkBranch("S", "A", 1.0, 0.5, 100_000.0),
            NetworkBranch("A", "B", 2.0, 1.0, 100_000.0),
        ],
        base_kv=12.47,
        bus_of_node={NODE_A: "A", NODE_B: "B"},
    )
    kwargs.update(overrides)
    return NetworkTopology(**kwargs)


def _data(
    *,
    n_time: int,
    load_a: list[float],
    load_b: list[float],
    network_topology: NetworkTopology | None,
    with_utility: bool = True,
) -> DataContainer:
    dts = [datetime(2024, 1, 1, 0, 0) + timedelta(hours=i) for i in range(n_time)]
    kwargs: dict[str, Any] = dict(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            NODE_A: load_a,
            NODE_B: load_b,
        },
        static={
            "electricity_load_keys": [NODE_A, NODE_B],
            "time_step_hours": 1.0,
        },
        network_topology=network_topology,
    )
    if with_utility:
        kwargs["import_prices_by_node"] = {NODE_A: [0.0] * n_time, NODE_B: [0.0] * n_time}
        kwargs["utility_rate_by_node"] = {NODE_A: None, NODE_B: None}
    return DataContainer(**kwargs)


# --------------------------------------------------------------------------------------
# 1. Hand-computed voltage on a 3-bus radial feeder.
# --------------------------------------------------------------------------------------


@requires_solver
def test_hand_computed_voltage_drop():
    p_a, p_b = 50.0, 30.0
    data = _data(n_time=1, load_a=[p_a], load_b=[p_b], network_topology=_feeder_topology())
    m = build_model(data, technology_parameters={}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    s, t = "_default", 0
    assert pyo.value(m.utility.grid_import[NODE_A, s, t]) == pytest.approx(p_a)
    assert pyo.value(m.utility.grid_import[NODE_B, s, t]) == pytest.approx(p_b)

    scale = 1.0 / 1000.0 / (12.47 ** 2)
    r1, r2 = 1.0, 2.0
    # V_child^2 = V_parent^2 - 2*(r*P_downstream)/1000/base_kv^2 (reactance term is zero:
    # unity power factor by default => q_inj = 0).
    expected_v_a_sq = 1.0 - 2 * r1 * scale * (p_a + p_b)
    expected_v_b_sq = 1.0 - (2 * r1 * scale * p_a + 2 * (r1 + r2) * scale * p_b)

    assert pyo.value(m.network.voltage_pu_sq["A", s, t]) == pytest.approx(expected_v_a_sq, abs=1e-12)
    assert pyo.value(m.network.voltage_pu_sq["B", s, t]) == pytest.approx(expected_v_b_sq, abs=1e-12)


# --------------------------------------------------------------------------------------
# 2. Branch-flow aggregation.
# --------------------------------------------------------------------------------------


@requires_solver
def test_branch_flow_aggregation():
    p_a, p_b = 50.0, 30.0
    data = _data(n_time=1, load_a=[p_a], load_b=[p_b], network_topology=_feeder_topology())
    m = build_model(data, technology_parameters={}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal

    s, t = "_default", 0
    assert pyo.value(m.network.p_flow["S", "A", s, t]) == pytest.approx(p_a + p_b)
    assert pyo.value(m.network.p_flow["A", "B", s, t]) == pytest.approx(p_b)


# --------------------------------------------------------------------------------------
# 3. Voltage limit binding -> infeasible.
# --------------------------------------------------------------------------------------


@requires_solver
def test_voltage_limit_violation_is_infeasible():
    topo = _feeder_topology(branch_limits_enabled=False)
    # Large load at B pulls voltage well below the default 0.95 p.u. lower bound.
    data = _data(n_time=1, load_a=[0.0], load_b=[3000.0], network_topology=topo)
    m = build_model(data, technology_parameters={}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition in (
        pyo.TerminationCondition.infeasible,
        pyo.TerminationCondition.infeasibleOrUnbounded,
    )


@requires_solver
def test_within_voltage_limit_is_feasible():
    """Sanity counterpart to the above: a small load on the same feeder stays feasible."""
    topo = _feeder_topology(branch_limits_enabled=False)
    data = _data(n_time=1, load_a=[0.0], load_b=[10.0], network_topology=topo)
    m = build_model(data, technology_parameters={}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition == pyo.TerminationCondition.optimal


# --------------------------------------------------------------------------------------
# 4. Thermal limit binding -> infeasible.
# --------------------------------------------------------------------------------------


@requires_solver
def test_thermal_limit_violation_is_infeasible():
    topo = _feeder_topology(
        voltage_limits_enabled=False,
        branches=[
            NetworkBranch("S", "A", 1.0, 0.5, 100_000.0),
            NetworkBranch("A", "B", 2.0, 1.0, 10.0),  # tiny rating on A-B
        ],
    )
    data = _data(n_time=1, load_a=[0.0], load_b=[1000.0], network_topology=topo)
    m = build_model(data, technology_parameters={}, financials={})
    result = _solve(m)
    assert result.solver.termination_condition in (
        pyo.TerminationCondition.infeasible,
        pyo.TerminationCondition.infeasibleOrUnbounded,
    )


# --------------------------------------------------------------------------------------
# 7. Islanded (no utility block) + network configured -> clear ValueError.
# --------------------------------------------------------------------------------------


def test_network_without_utility_block_raises_clear_error():
    data = _data(
        n_time=1, load_a=[0.0], load_b=[0.0], network_topology=_feeder_topology(), with_utility=False
    )
    with pytest.raises(ValueError, match="utility_mode"):
        build_model(data, technology_parameters={}, financials={})


# --------------------------------------------------------------------------------------
# 8. No-network regression: today's behavior is unaffected when network_topology is unset.
# --------------------------------------------------------------------------------------


def test_no_network_topology_attaches_nothing():
    data = _data(n_time=1, load_a=[0.0], load_b=[0.0], network_topology=None)
    m = build_model(data, technology_parameters={}, financials={})
    assert not hasattr(m, "network")
