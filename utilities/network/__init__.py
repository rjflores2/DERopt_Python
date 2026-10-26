"""Electrical network (LinDistFlow) utility package.

Ports the linearized DistFlow formulation from the original MATLAB DERopt model
(``opt_LinDistFlow.m``) as a set of Pyomo constraints layered on top of each node's already-
existing ``model.utility.grid_import`` -- bus voltage (p.u.²), branch real/reactive power
flow, and optional voltage/thermal limits, for a strictly radial (tree) feeder.

Like ``utilities.electricity_import_export``, this is NOT a ``technologies.REGISTRY`` entry:
``model.core`` calls ``register`` directly, after the utility block (this block's ``p_inj``
reads ``model.utility.grid_import``, so the utility block must already be attached; this
first version therefore requires a grid-tied case -- see ``block.add_network_block`` for the
fail-fast check). Fully opt-in: returns ``None`` (attaches nothing) when
``data.network_topology`` is unset, so cases without a network are unaffected.

Layout:

- ``inputs`` -- ``NetworkBranch``/``NetworkTopology`` (user-facing shapes) and
  ``resolve_network_block_inputs`` (radial-tree validation + BFS-derived coefficients).
- ``block`` -- the Pyomo block itself.
- ``reporting`` -- optional ``collect_block_report`` hook (voltage/branch-loading diagnostics).
"""

from __future__ import annotations

from .block import add_network_block, register
from .inputs import NetworkBranch, NetworkTopology, ResolvedNetworkInputs, resolve_network_block_inputs

__all__ = [
    "NetworkBranch",
    "NetworkTopology",
    "ResolvedNetworkInputs",
    "add_network_block",
    "register",
    "resolve_network_block_inputs",
]
