"""Pure-Python validation/derivation tests for ``utilities.network.inputs`` (no Pyomo/solver)."""

from __future__ import annotations

import pytest

from utilities.network.inputs import NetworkBranch, NetworkTopology, resolve_network_block_inputs


def _chain_topology(**overrides) -> NetworkTopology:
    """Slack S -> A -> B, r=(1.0, 2.0) ohm, x=(0.5, 1.0) ohm, base_kv=12.47."""
    kwargs = dict(
        buses=["S", "A", "B"],
        slack_bus="S",
        branches=[
            NetworkBranch("S", "A", 1.0, 0.5, 100.0),
            NetworkBranch("A", "B", 2.0, 1.0, 50.0),
        ],
        base_kv=12.47,
    )
    kwargs.update(overrides)
    return NetworkTopology(**kwargs)


def test_resolve_valid_radial_feeder():
    r = resolve_network_block_inputs(_chain_topology(), nodes=["A", "B"])
    assert r.nonslack_buses == ["A", "B"]
    assert r.branch_keys == [("S", "A"), ("A", "B")]
    assert r.branch_subtree_buses == {("S", "A"): ["A", "B"], ("A", "B"): ["B"]}
    assert r.bus_of_node == {"A": "A", "B": "B"}
    assert r.node_of_bus == {"A": "A", "B": "B"}
    assert r.power_factor_by_bus == {"A": 1.0, "B": 1.0}
    assert r.rating_kva == {("S", "A"): 100.0, ("A", "B"): 50.0}

    scale = 1.0 / 1000.0 / (12.47 ** 2)
    assert r.r_coeff_pu[("A", "A")] == pytest.approx(2 * 1.0 * scale)
    assert r.r_coeff_pu[("A", "B")] == pytest.approx(2 * 1.0 * scale)
    assert r.r_coeff_pu[("B", "A")] == pytest.approx(2 * 1.0 * scale)
    assert r.r_coeff_pu[("B", "B")] == pytest.approx(2 * 3.0 * scale)
    assert r.x_coeff_pu[("B", "B")] == pytest.approx(2 * 1.5 * scale)


def test_orientation_agnostic_direction_derived_from_slack():
    """Swapping from_bus/to_bus on a branch must not change the resolved coefficients --
    the BFS-derived parent/child direction, not the raw input order, drives the math."""
    swapped = _chain_topology(
        branches=[
            NetworkBranch("A", "S", 1.0, 0.5, 100.0),  # swapped
            NetworkBranch("A", "B", 2.0, 1.0, 50.0),
        ]
    )
    r_normal = resolve_network_block_inputs(_chain_topology(), nodes=["A", "B"])
    r_swapped = resolve_network_block_inputs(swapped, nodes=["A", "B"])
    assert r_swapped.r_coeff_pu == r_normal.r_coeff_pu
    assert r_swapped.x_coeff_pu == r_normal.x_coeff_pu
    assert r_swapped.branch_keys == r_normal.branch_keys


def test_rejects_missing_slack_bus():
    with pytest.raises(ValueError, match="slack_bus"):
        resolve_network_block_inputs(_chain_topology(slack_bus="not_a_bus"), nodes=[])


def test_rejects_wrong_branch_count():
    topo = _chain_topology(
        buses=["S", "A", "B", "C"],
        branches=[NetworkBranch("S", "A", 1.0, 0.5, 100.0), NetworkBranch("A", "B", 1.0, 0.5, 100.0)],
    )
    with pytest.raises(ValueError, match="exactly 3 branches"):
        resolve_network_block_inputs(topo, nodes=[])


def test_rejects_cycle_plus_disconnected_bus():
    """N=4 buses, N-1=3 branches (passes the count check), but arranged as a 3-cycle among
    S/A/B plus a fully disconnected bus C -- the count check alone cannot catch this; only
    BFS reachability can."""
    topo = _chain_topology(
        buses=["S", "A", "B", "C"],
        branches=[
            NetworkBranch("S", "A", 1.0, 0.5, 100.0),
            NetworkBranch("A", "B", 1.0, 0.5, 100.0),
            NetworkBranch("B", "S", 1.0, 0.5, 100.0),
        ],
    )
    with pytest.raises(ValueError, match="unreachable buses.*'C'"):
        resolve_network_block_inputs(topo, nodes=[])


def test_accepts_star_topology():
    """Sanity check that a legitimate non-chain radial tree (three independent feeders off
    one slack) is accepted, not rejected."""
    topo = _chain_topology(
        buses=["S", "A", "B", "C"],
        branches=[
            NetworkBranch("S", "A", 1.0, 0.5, 100.0),
            NetworkBranch("S", "B", 1.0, 0.5, 100.0),
            NetworkBranch("S", "C", 1.0, 0.5, 100.0),
        ],
    )
    r = resolve_network_block_inputs(topo, nodes=[])
    assert set(r.nonslack_buses) == {"A", "B", "C"}


def test_rejects_unmapped_node():
    with pytest.raises(ValueError, match="not_a_bus"):
        resolve_network_block_inputs(_chain_topology(), nodes=["not_a_bus"])


def test_bus_of_node_override_allows_distinct_names():
    topo = _chain_topology(bus_of_node={"building_1": "A", "building_2": "B"})
    r = resolve_network_block_inputs(topo, nodes=["building_1", "building_2"])
    assert r.bus_of_node == {"building_1": "A", "building_2": "B"}
    assert r.node_of_bus == {"A": "building_1", "B": "building_2"}


def test_junction_bus_with_no_mapped_node_is_allowed():
    """A bus with no associated node (e.g. a pole/junction) is valid and simply absent from
    node_of_bus; the network block treats it as p_inj = 0."""
    r = resolve_network_block_inputs(_chain_topology(), nodes=["A"])  # B has no node
    assert "B" not in r.node_of_bus
    assert r.node_of_bus == {"A": "A"}


def test_power_factor_default_and_override():
    r_default = resolve_network_block_inputs(_chain_topology(), nodes=[])
    assert r_default.power_factor_by_bus == {"A": 1.0, "B": 1.0}

    r_override = resolve_network_block_inputs(
        _chain_topology(power_factor_by_bus={"A": 0.9}), nodes=[]
    )
    assert r_override.power_factor_by_bus == {"A": 0.9, "B": 1.0}


def test_rejects_out_of_range_power_factor():
    with pytest.raises(ValueError, match="power_factor"):
        resolve_network_block_inputs(_chain_topology(power_factor_by_bus={"A": 1.5}), nodes=[])
    with pytest.raises(ValueError, match="power_factor"):
        resolve_network_block_inputs(_chain_topology(power_factor_by_bus={"A": 0.0}), nodes=[])


def test_rejects_negative_resistance_or_reactance():
    topo = _chain_topology(branches=[NetworkBranch("S", "A", -1.0, 0.5, 100.0)], buses=["S", "A"])
    with pytest.raises(ValueError, match="resistance_ohm"):
        resolve_network_block_inputs(topo, nodes=[])


def test_rejects_nonpositive_rating():
    topo = _chain_topology(branches=[NetworkBranch("S", "A", 1.0, 0.5, 0.0)], buses=["S", "A"])
    with pytest.raises(ValueError, match="rating_kva"):
        resolve_network_block_inputs(topo, nodes=[])


def test_rejects_nonpositive_base_kv():
    with pytest.raises(ValueError, match="base_kv"):
        resolve_network_block_inputs(_chain_topology(base_kv=0.0), nodes=[])


def test_rejects_invalid_voltage_limit_pu():
    with pytest.raises(ValueError, match="voltage_limit_pu"):
        resolve_network_block_inputs(_chain_topology(voltage_limit_pu=(1.05, 0.95)), nodes=[])


def test_rejects_nonpositive_branch_thermal_margin():
    with pytest.raises(ValueError, match="branch_thermal_margin"):
        resolve_network_block_inputs(_chain_topology(branch_thermal_margin=0.0), nodes=[])


def test_rejects_too_few_polygon_sides():
    with pytest.raises(ValueError, match="branch_polygon_sides"):
        resolve_network_block_inputs(_chain_topology(branch_polygon_sides=2), nodes=[])
