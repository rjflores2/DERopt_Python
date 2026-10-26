"""Per-block reporting hook for the network layer.

Discovered by ``utilities.reporting.overarching_template`` via ``blk._technology_module``
(set by ``model.core``), same discovery mechanism used by technology blocks.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from data_loading.schemas import DataContainer


def collect_block_report(
    model: pyo.Block,
    block: pyo.Block,
    data: DataContainer,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Voltage-profile and branch-loading diagnostics for the network block."""
    SCENARIOS = ctx["SCENARIOS"]
    T = ctx["T"]
    if not hasattr(block, "voltage_pu_sq"):
        return {}

    min_voltage_pu = None
    max_voltage_pu = None
    for bus in block.NONSLACK_BUSES:
        for s in SCENARIOS:
            for t in T:
                v = float(pyo.value(block.voltage_pu_sq[bus, s, t])) ** 0.5
                if min_voltage_pu is None or v < min_voltage_pu:
                    min_voltage_pu = v
                if max_voltage_pu is None or v > max_voltage_pu:
                    max_voltage_pu = v

    worst_branch = None
    worst_branch_loading_fraction = 0.0
    for parent, child in block.BRANCHES:
        rating = float(pyo.value(block.rating_kva[parent, child]))
        for s in SCENARIOS:
            for t in T:
                p = float(pyo.value(block.p_flow[parent, child, s, t]))
                q = float(pyo.value(block.q_flow[parent, child, s, t]))
                loading = ((p ** 2 + q ** 2) ** 0.5) / rating if rating > 0 else 0.0
                if loading > worst_branch_loading_fraction:
                    worst_branch_loading_fraction = loading
                    worst_branch = (parent, child)

    return {
        "min_voltage_pu": min_voltage_pu,
        "max_voltage_pu": max_voltage_pu,
        "worst_branch": worst_branch,
        "worst_branch_loading_fraction": worst_branch_loading_fraction,
    }
