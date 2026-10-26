"""Network topology validation and closed-form LinDistFlow coefficient derivation.

Ports the linearized DistFlow ("LinDistFlow") formulation from the original MATLAB DERopt
model (``opt_LinDistFlow.m``, itself based on Eran Schweitzer's public ``distflow``
closed-form matrix implementation) as a graph/BFS computation instead of a literal sparse
matrix inversion. For a radial tree rooted at a slack bus, the standard Baran & Wu result is:

    voltage_pu_sq[j] = 1.0 - sum_k( r_coeff_pu[j,k] * p_inj[k] + x_coeff_pu[j,k] * q_inj[k] )

(positive ``p_inj``, i.e. net load draw from the network, pulls voltage down -- the standard
"voltage drop under load" result) where ``r_coeff_pu[j,k]`` is ``2 * (resistance shared by the
path from the slack to j and the path from the slack to k) / 1000 / base_kv**2`` -- i.e. twice
the resistance from the slack to the lowest common ancestor of ``j`` and ``k`` on the tree.
This is mathematically identical to the MATLAB ``R = 2*(M\\Rline)*(C+I)*To`` matrix
construction (verified by hand-deriving the matrix for a trivial 2-bus case: despite the "+"
in the MATLAB formula's outer expression, the R matrix it multiplies comes out negative there,
so the net effect is subtractive, matching this module), derived here directly from one BFS
per case instead of a matrix inversion -- easier to hand-verify (count shared branches) and
free of any numerical-conditioning concerns from inverting a (possibly large) sparse matrix.

Branch ``from_bus``/``to_bus`` orientation in the input is NOT trusted for parent/child
direction: the BFS derives the true parent-to-child direction outward from the slack bus, so
an accidentally-swapped ``from_bus``/``to_bus`` on one branch cannot silently produce wrong
voltage numbers (a real hazard in the literal MATLAB approach, which assumes correct
orientation and would otherwise fail silently).

Reactive power is not separately dispatched by any technology (matches the legacy MATLAB
model): ``q_inj[bus] = p_inj[bus] * tan(acos(power_factor[bus]))``, a fixed per-bus
assumption.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class NetworkBranch:
    """One line/branch connecting two buses. ``from_bus``/``to_bus`` are unordered endpoints
    -- see module docstring on why orientation is derived, not trusted, from the input."""

    from_bus: str
    to_bus: str
    resistance_ohm: float
    reactance_ohm: float
    rating_kva: float


@dataclass
class NetworkTopology:
    """User-supplied network topology and settings.

    ``buses``, ``slack_bus``, ``branches``, and ``base_kv`` have no default: physical
    infrastructure, where a wrong guess is worse than a failure. ``bus_of_node`` defaults to
    identity (node name == bus name) when omitted; buses with no mapped node (e.g. junction/
    pole buses) get ``p_inj = q_inj = 0``. ``power_factor_by_bus`` defaults to 1.0 (unity,
    i.e. zero reactive power) for any bus not given an explicit value.
    """

    buses: list[str]
    slack_bus: str
    branches: list[NetworkBranch]
    base_kv: float
    bus_of_node: dict[str, str] | None = None
    power_factor_by_bus: dict[str, float] | None = None
    voltage_limit_pu: tuple[float, float] = (0.95, 1.05)
    voltage_limits_enabled: bool = True
    branch_thermal_margin: float = 1.0
    branch_limits_enabled: bool = True
    branch_polygon_sides: int = 22


@dataclass
class ResolvedNetworkInputs:
    """Fully validated/derived inputs consumed by ``utilities.network.block``."""

    nonslack_buses: list[str]
    slack_bus: str
    branch_keys: list[tuple[str, str]]
    r_coeff_pu: dict[tuple[str, str], float]
    x_coeff_pu: dict[tuple[str, str], float]
    branch_subtree_buses: dict[tuple[str, str], list[str]]
    rating_kva: dict[tuple[str, str], float]
    bus_of_node: dict[str, str]
    node_of_bus: dict[str, str]
    power_factor_by_bus: dict[str, float]
    voltage_limit_pu: tuple[float, float]
    voltage_limits_enabled: bool
    branch_thermal_margin: float
    branch_limits_enabled: bool
    branch_polygon_sides: int
    base_kv: float


def resolve_network_block_inputs(
    topology: NetworkTopology,
    *,
    nodes: list[str],
) -> ResolvedNetworkInputs:
    """Validate ``topology`` (radial tree, node mappings) and derive BFS-based coefficients.

    Raises ``ValueError`` with a specific, actionable message for every failure mode: missing
    slack bus, wrong branch count, unknown branch endpoints, a non-radial (disconnected or
    cyclic) branch graph, an unmapped ``model.NODES`` entry, or an out-of-range scalar
    setting.
    """
    buses = list(topology.buses)
    bus_set = set(buses)
    if len(bus_set) != len(buses):
        raise ValueError(f"network: duplicate bus keys in buses={buses!r}")

    if topology.slack_bus not in bus_set:
        raise ValueError(
            f"network: slack_bus {topology.slack_bus!r} is not in buses={buses!r}"
        )

    if topology.base_kv <= 0:
        raise ValueError(f"network: base_kv must be > 0, got {topology.base_kv}")

    if len(topology.branches) != len(buses) - 1:
        raise ValueError(
            f"network: a radial tree over {len(buses)} buses requires exactly "
            f"{len(buses) - 1} branches, got {len(topology.branches)}"
        )

    for br in topology.branches:
        if br.from_bus not in bus_set:
            raise ValueError(f"network: branch from_bus {br.from_bus!r} is not in buses={buses!r}")
        if br.to_bus not in bus_set:
            raise ValueError(f"network: branch to_bus {br.to_bus!r} is not in buses={buses!r}")
        if br.resistance_ohm < 0 or br.reactance_ohm < 0:
            raise ValueError(
                f"network: branch ({br.from_bus!r}, {br.to_bus!r}) resistance_ohm and "
                f"reactance_ohm must be >= 0, got {br.resistance_ohm}, {br.reactance_ohm}"
            )
        if br.rating_kva <= 0:
            raise ValueError(
                f"network: branch ({br.from_bus!r}, {br.to_bus!r}) rating_kva must be > 0, "
                f"got {br.rating_kva}"
            )

    vlow, vhigh = topology.voltage_limit_pu
    if not (0 < vlow < vhigh):
        raise ValueError(
            f"network: voltage_limit_pu must be (low, high) with 0 < low < high, got "
            f"{topology.voltage_limit_pu}"
        )
    if topology.branch_thermal_margin <= 0:
        raise ValueError(
            f"network: branch_thermal_margin must be > 0, got {topology.branch_thermal_margin}"
        )
    if topology.branch_polygon_sides < 3:
        raise ValueError(
            f"network: branch_polygon_sides must be >= 3, got {topology.branch_polygon_sides}"
        )

    # --- Undirected adjacency for BFS (orientation of the raw input is not trusted). ---
    adjacency: dict[str, list[tuple[str, NetworkBranch]]] = {b: [] for b in buses}
    for br in topology.branches:
        adjacency[br.from_bus].append((br.to_bus, br))
        adjacency[br.to_bus].append((br.from_bus, br))

    slack_bus = topology.slack_bus
    parent_of: dict[str, str] = {}
    branch_of: dict[str, NetworkBranch] = {}  # non-slack bus -> its edge to parent_of[bus]
    visit_order: list[str] = []
    visited = {slack_bus}
    queue: deque[str] = deque([slack_bus])
    while queue:
        cur = queue.popleft()
        for neighbor, br in adjacency[cur]:
            if neighbor in visited:
                continue
            visited.add(neighbor)
            parent_of[neighbor] = cur
            branch_of[neighbor] = br
            visit_order.append(neighbor)
            queue.append(neighbor)

    if len(visited) != len(buses):
        unreached = sorted(bus_set - visited)
        raise ValueError(
            f"network: branches do not form a radial tree reaching every bus from "
            f"slack_bus={slack_bus!r}; unreachable buses: {unreached}. Exactly "
            f"{len(buses) - 1} branches connecting every bus in one connected, acyclic graph "
            f"is required (a valid radial feeder), not just the right branch count."
        )

    branch_keys: list[tuple[str, str]] = [(parent_of[b], b) for b in visit_order]

    # --- Cumulative path resistance/reactance from slack to each non-slack bus. ---
    path_r: dict[str, float] = {slack_bus: 0.0}
    path_x: dict[str, float] = {slack_bus: 0.0}
    path_buses: dict[str, list[str]] = {slack_bus: []}
    rating_kva: dict[tuple[str, str], float] = {}
    for bus in visit_order:
        parent = parent_of[bus]
        br = branch_of[bus]
        path_r[bus] = path_r[parent] + br.resistance_ohm
        path_x[bus] = path_x[parent] + br.reactance_ohm
        path_buses[bus] = path_buses[parent] + [bus]
        rating_kva[(parent, bus)] = br.rating_kva

    # --- Voltage-sensitivity coefficients: 2 * (resistance/reactance shared by the paths to
    # j and k), i.e. 2 * path-to-LCA(j, k). ---
    scale = 1.0 / 1000.0 / (topology.base_kv ** 2)
    r_coeff_pu: dict[tuple[str, str], float] = {}
    x_coeff_pu: dict[tuple[str, str], float] = {}
    for j in visit_order:
        for k in visit_order:
            shared_len = 0
            for a, b in zip(path_buses[j], path_buses[k]):
                if a != b:
                    break
                shared_len += 1
            if shared_len == 0:
                lca_r = 0.0
                lca_x = 0.0
            else:
                lca_bus = path_buses[j][shared_len - 1]
                lca_r = path_r[lca_bus]
                lca_x = path_x[lca_bus]
            r_coeff_pu[(j, k)] = 2.0 * lca_r * scale
            x_coeff_pu[(j, k)] = 2.0 * lca_x * scale

    # --- Branch downstream-subtree membership (for p_flow/q_flow aggregation). ---
    branch_subtree_buses: dict[tuple[str, str], list[str]] = {key: [] for key in branch_keys}
    for bus in visit_order:
        cur = bus
        while cur != slack_bus:
            parent = parent_of[cur]
            branch_subtree_buses[(parent, cur)].append(bus)
            cur = parent

    # --- Node -> bus resolution (identity default), and its inverse. ---
    bus_of_node: dict[str, str] = {}
    node_of_bus: dict[str, str] = {}
    raw_bus_of_node = topology.bus_of_node or {}
    for node in nodes:
        bus = raw_bus_of_node.get(node, node)
        if bus not in bus_set:
            raise ValueError(
                f"network: node {node!r} maps to bus {bus!r}, which is not in "
                f"buses={buses!r}. Set NetworkTopology.bus_of_node[{node!r}] to a declared bus."
            )
        bus_of_node[node] = bus
        node_of_bus[bus] = node

    power_factor_by_bus: dict[str, float] = {}
    raw_pf = topology.power_factor_by_bus or {}
    for bus in visit_order:
        pf = float(raw_pf.get(bus, 1.0))
        if not (0 < pf <= 1):
            raise ValueError(f"network: power_factor for bus {bus!r} must be in (0, 1], got {pf}")
        power_factor_by_bus[bus] = pf

    return ResolvedNetworkInputs(
        nonslack_buses=visit_order,
        slack_bus=slack_bus,
        branch_keys=branch_keys,
        r_coeff_pu=r_coeff_pu,
        x_coeff_pu=x_coeff_pu,
        branch_subtree_buses=branch_subtree_buses,
        rating_kva=rating_kva,
        bus_of_node=bus_of_node,
        node_of_bus=node_of_bus,
        power_factor_by_bus=power_factor_by_bus,
        voltage_limit_pu=topology.voltage_limit_pu,
        voltage_limits_enabled=topology.voltage_limits_enabled,
        branch_thermal_margin=topology.branch_thermal_margin,
        branch_limits_enabled=topology.branch_limits_enabled,
        branch_polygon_sides=topology.branch_polygon_sides,
        base_kv=topology.base_kv,
    )


def polygon_coefficients(sides: int) -> list[tuple[float, float]]:
    """Return ``[(cos(theta_l), sin(theta_l)), ...]`` for an ``sides``-sided polygon
    approximation of a circle, rotated so a flat edge (not a vertex) faces the P axis --
    matches the MATLAB ``thetab = pi/Lb + ib.*(2*pi)./Lb`` construction exactly, so
    ``margin * rating_kva * cos(pi/sides)`` is the correct per-side bound (the polygon's
    flat edges are inscribed exactly at the true circle radius)."""
    return [
        (math.cos(math.pi / sides + i * (2.0 * math.pi / sides)), math.sin(math.pi / sides + i * (2.0 * math.pi / sides)))
        for i in range(sides)
    ]
