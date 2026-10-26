"""Pyomo LinDistFlow network block: bus voltage, branch flow, thermal/voltage limits.

Read-only physical-consequence layer over dispatch the model already computes: it adds no
new energy source or sink (no ``electricity_source_term``/``electricity_sink_term``, no
``objective_contribution``), just constraints on the voltage/branch-loading consequences of
each node's existing ``model.utility.grid_import``. See ``utilities/network/inputs.py`` for
the closed-form math this ports and ``requirements`` (none written yet for this feature; see
the approved plan) for the grid-tied-only scope of this first version.
"""

from __future__ import annotations

import math

import pyomo.environ as pyo

from data_loading.schemas import DataContainer

from .inputs import polygon_coefficients, resolve_network_block_inputs


def add_network_block(model: pyo.Block, data: DataContainer) -> pyo.Block | None:
    """Build and attach the network block, or return ``None`` if no topology is configured.

    Requires ``model.utility`` (the grid/utility block, see
    ``utilities.electricity_import_export``) to already be attached: this first version's
    ``p_inj`` reads each mapped bus's ``model.utility.grid_import``, so a network can only be
    layered onto a grid-tied case (``utility_mode`` other than ``"islanded"``). Off-grid
    inter-node power sharing is a separate, larger follow-on (see the approved plan).
    """
    topology = getattr(data, "network_topology", None)
    if topology is None:
        return None

    if getattr(model, "utility", None) is None:
        raise ValueError(
            "network: data.network_topology is set but model.utility is not attached. "
            "This first version's network layer reads model.utility.grid_import as each "
            "bus's net real power draw, so it requires a grid-tied case (CaseConfig.utility_mode "
            "must be 'priced_grid' or 'free_grid', not 'islanded')."
        )

    T = model.T
    nodes = list(model.NODES)
    resolved = resolve_network_block_inputs(topology, nodes=nodes)

    tan_phi = {
        bus: math.tan(math.acos(pf)) for bus, pf in resolved.power_factor_by_bus.items()
    }
    polygon = polygon_coefficients(resolved.branch_polygon_sides)
    polygon_rhs_scale = math.cos(math.pi / resolved.branch_polygon_sides)

    def block_rule(net_block):
        net_block.NONSLACK_BUSES = pyo.Set(initialize=resolved.nonslack_buses, ordered=True)
        net_block.BRANCHES = pyo.Set(dimen=2, initialize=resolved.branch_keys, ordered=True)

        net_block.r_coeff_pu = pyo.Param(
            net_block.NONSLACK_BUSES,
            net_block.NONSLACK_BUSES,
            initialize=resolved.r_coeff_pu,
            within=pyo.Reals,
            mutable=False,
        )
        net_block.x_coeff_pu = pyo.Param(
            net_block.NONSLACK_BUSES,
            net_block.NONSLACK_BUSES,
            initialize=resolved.x_coeff_pu,
            within=pyo.Reals,
            mutable=False,
        )
        net_block.rating_kva = pyo.Param(
            net_block.BRANCHES,
            initialize=resolved.rating_kva,
            within=pyo.PositiveReals,
            mutable=False,
        )

        net_block.voltage_pu_sq = pyo.Var(
            net_block.NONSLACK_BUSES, model.SCENARIOS, T, within=pyo.NonNegativeReals
        )

        def p_inj_rule(m, bus, s, t):
            node = resolved.node_of_bus.get(bus)
            if node is None:
                return 0.0
            return model.utility.grid_import[node, s, t]

        net_block.p_inj = pyo.Expression(
            net_block.NONSLACK_BUSES, model.SCENARIOS, T, rule=p_inj_rule
        )
        net_block.q_inj = pyo.Expression(
            net_block.NONSLACK_BUSES,
            model.SCENARIOS,
            T,
            rule=lambda m, bus, s, t: m.p_inj[bus, s, t] * tan_phi[bus],
        )

        def voltage_balance_rule(m, bus, s, t):
            # V_child^2 = V_parent^2 - 2*(r*P + x*Q) summed along the path to the slack
            # (standard Baran-Wu linearized DistFlow): positive p_inj (net load draw from
            # the network) pulls voltage DOWN. Verified against the MATLAB source's actual
            # matrix values (not just the "+" in its outer formula, which is misleading on
            # its own -- the R matrix it multiplies is itself negative; the net effect is
            # subtractive) via a hand-derived trivial 2-bus case.
            return m.voltage_pu_sq[bus, s, t] == 1.0 - sum(
                m.r_coeff_pu[bus, k] * m.p_inj[k, s, t] + m.x_coeff_pu[bus, k] * m.q_inj[k, s, t]
                for k in m.NONSLACK_BUSES
            )

        net_block.voltage_balance = pyo.Constraint(
            net_block.NONSLACK_BUSES, model.SCENARIOS, T, rule=voltage_balance_rule
        )

        def p_flow_rule(m, parent, child, s, t):
            return sum(
                m.p_inj[bus, s, t] for bus in resolved.branch_subtree_buses[(parent, child)]
            )

        def q_flow_rule(m, parent, child, s, t):
            return sum(
                m.q_inj[bus, s, t] for bus in resolved.branch_subtree_buses[(parent, child)]
            )

        net_block.p_flow = pyo.Expression(
            net_block.BRANCHES, model.SCENARIOS, T, rule=p_flow_rule
        )
        net_block.q_flow = pyo.Expression(
            net_block.BRANCHES, model.SCENARIOS, T, rule=q_flow_rule
        )

        if resolved.branch_limits_enabled:
            polygon_index = list(range(resolved.branch_polygon_sides))

            def thermal_polygon_rule(m, parent, child, side, s, t):
                cos_l, sin_l = polygon[side]
                return (
                    cos_l * m.p_flow[parent, child, s, t] + sin_l * m.q_flow[parent, child, s, t]
                    <= resolved.branch_thermal_margin * m.rating_kva[parent, child] * polygon_rhs_scale
                )

            net_block.branch_thermal_polygon = pyo.Constraint(
                net_block.BRANCHES, polygon_index, model.SCENARIOS, T, rule=thermal_polygon_rule
            )

        if resolved.voltage_limits_enabled:
            vlow, vhigh = resolved.voltage_limit_pu

            def voltage_box_rule(m, bus, s, t):
                return (vlow ** 2, m.voltage_pu_sq[bus, s, t], vhigh ** 2)

            net_block.voltage_box = pyo.Constraint(
                net_block.NONSLACK_BUSES, model.SCENARIOS, T, rule=voltage_box_rule
            )

    model.network = pyo.Block(rule=block_rule)
    return model.network


def register(model: pyo.Block, data: DataContainer) -> pyo.Block | None:
    """Registry hook used directly by ``model.core`` (not part of ``technologies.REGISTRY``)."""
    return add_network_block(model, data)
