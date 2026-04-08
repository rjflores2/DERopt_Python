"""Hydrokinetic technology block: LP (continuous area + kW) and unit MILP."""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

from .inputs import (
    FORMULATION_HYDROKINETIC_LP,
    FORMULATION_HYDROKINETIC_UNIT_MILP,
    resolve_hydrokinetic_block_inputs,
)


def add_hydrokinetic_block(
    model: Any,
    data: Any,
    *,
    hydrokinetic_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """Attach hydrokinetic generation (run-of-river style) to the model.

    Resource: ``data.static['hydrokinetic_production_keys']`` and matching timeseries (kWh/kW per
    period). Scaling to swept area uses ``data.static['hydrokinetic_reference_swept_area_m2']`` and
    ``data.static['hydrokinetic_reference_kw']`` (see ``technologies.hydrokinetic.inputs``).

    LP (``hydrokinetic_lp``): continuous adopted swept area and kW; generation <= area * yield_m2[t]
    and <= kW * dt_hours; ``kW <= density * total_swept_area``.

    MILP (``hydrokinetic_unit_milp``): integer units per (node, profile); generation <= units *
    unit_swept_area * yield_m2[t] and <= units * unit_kw * dt_hours.
    """
    T = model.T
    nodes = list(model.NODES)
    T_list = list(T)

    profiles = list(data.static.get("hydrokinetic_production_keys") or [])
    if not profiles:
        raise ValueError(
            "hydrokinetic block requires data.static['hydrokinetic_production_keys'] (load HKT data first)"
        )

    ref_area = data.static.get("hydrokinetic_reference_swept_area_m2")
    if ref_area is None:
        raise ValueError(
            "hydrokinetic block requires data.static['hydrokinetic_reference_swept_area_m2'] "
            "(m² of the device used to produce the resource file — set via CaseConfig or "
            "load_hydrokinetic_into_container(..., reference_swept_area_m2=...))."
        )
    ref_kw = float(data.static.get("hydrokinetic_reference_kw") or 1.0)

    production_by_profile = {key: list(data.timeseries[key]) for key in profiles}
    n_time = len(T_list)
    for key in profiles:
        if len(production_by_profile[key]) != n_time:
            raise ValueError(
                f"hydrokinetic: timeseries[{key!r}] length {len(production_by_profile[key])} != {n_time}"
            )

    dt_hours = float(data.static.get("time_step_hours") or 1.0)
    resolved = resolve_hydrokinetic_block_inputs(
        hydrokinetic_params,
        financials,
        nodes,
        profiles,
        production_by_profile,
        reference_kw=ref_kw,
        reference_swept_area_m2=float(ref_area),
        time_indices=T_list,
        time_step_hours=dt_hours,
    )

    formulation = resolved.formulation
    allow_adoption = resolved.allow_adoption

    def block_rule(hk_block):
        hk_block.HKT = pyo.Set(initialize=profiles, ordered=True)

        hk_block.yield_kwh_per_m2 = pyo.Param(
            hk_block.HKT,
            T,
            initialize={
                (p, t): resolved.yield_kwh_per_m2_init[(p, t)]
                for p in profiles
                for t in T_list
            },
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.time_step_hours = pyo.Param(initialize=dt_hours, within=pyo.PositiveReals, mutable=True)

        hk_block.hkt_generation = pyo.Var(nodes, hk_block.HKT, T, within=pyo.NonNegativeReals)

        # --- Per-profile scalar params (Pyomo indexed by set) ---
        hk_block.capital_cost_per_kw = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.capital_cost_per_kw[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.capital_cost_per_m2 = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.capital_cost_per_m2[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.fixed_om_per_kw_year = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.fixed_om_per_kw_year[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.fixed_om_per_m2_year = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.fixed_om_per_m2_year[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.variable_om_per_kwh = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.variable_om_per_kwh[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.max_power_density_kw_per_m2 = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.max_power_density_kw_per_m2[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.unit_swept_area_m2 = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.unit_swept_area_m2[i] for i, p in enumerate(profiles)},
            within=pyo.PositiveReals,
            mutable=True,
        )
        hk_block.unit_capacity_kw = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.unit_capacity_kw[i] for i, p in enumerate(profiles)},
            within=pyo.PositiveReals,
            mutable=True,
        )
        hk_block.annual_capital_per_unit = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.annual_capital_per_unit[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.fixed_om_per_unit_year_param = pyo.Param(
            hk_block.HKT,
            initialize={p: resolved.fixed_om_per_unit_year[i] for i, p in enumerate(profiles)},
            within=pyo.NonNegativeReals,
            mutable=True,
        )

        hk_block.existing_swept_area_m2 = pyo.Param(
            nodes,
            hk_block.HKT,
            initialize={(n, p): resolved.existing_swept_area_m2[(n, p)] for n in nodes for p in profiles},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.existing_capacity_kw = pyo.Param(
            nodes,
            hk_block.HKT,
            initialize={(n, p): resolved.existing_capacity_kw[(n, p)] for n in nodes for p in profiles},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.max_swept_area_m2 = pyo.Param(
            nodes,
            hk_block.HKT,
            initialize={(n, p): resolved.max_swept_area_m2[(n, p)] for n in nodes for p in profiles},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        hk_block.max_installed_units = pyo.Param(
            nodes,
            hk_block.HKT,
            initialize={
                (n, p): resolved.max_installed_units_by_node_profile[(n, p)] for n in nodes for p in profiles
            },
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        hk_block.existing_units = pyo.Param(
            nodes,
            hk_block.HKT,
            initialize={(n, p): resolved.existing_units_by_node_profile[(n, p)] for n in nodes for p in profiles},
            within=pyo.NonNegativeIntegers,
            mutable=True,
        )
        hk_block.amortization_factor = pyo.Param(
            initialize=resolved.amortization_factor, within=pyo.NonNegativeReals, mutable=True
        )

        if formulation == FORMULATION_HYDROKINETIC_LP:

            def total_swept_area_expr(m, node, p):
                return m.existing_swept_area_m2[node, p] + (
                    m.swept_area_adopted[node, p] if allow_adoption else 0.0
                )

            def total_kw_expr(m, node, p):
                return m.existing_capacity_kw[node, p] + (
                    m.capacity_kw_adopted[node, p] if allow_adoption else 0.0
                )

            if allow_adoption:
                hk_block.swept_area_adopted = pyo.Var(nodes, hk_block.HKT, within=pyo.NonNegativeReals)
                hk_block.capacity_kw_adopted = pyo.Var(nodes, hk_block.HKT, within=pyo.NonNegativeReals)

            hk_block.total_swept_area_m2 = pyo.Expression(nodes, hk_block.HKT, rule=total_swept_area_expr)
            hk_block.total_capacity_kw = pyo.Expression(nodes, hk_block.HKT, rule=total_kw_expr)

            def gen_resource_limit(m, node, p, t):
                return m.hkt_generation[node, p, t] <= m.total_swept_area_m2[node, p] * m.yield_kwh_per_m2[p, t]

            def gen_nameplate_limit(m, node, p, t):
                return m.hkt_generation[node, p, t] <= m.total_capacity_kw[node, p] * m.time_step_hours

            hk_block.generation_resource_limit = pyo.Constraint(
                nodes, hk_block.HKT, T, rule=gen_resource_limit
            )
            hk_block.generation_nameplate_limit = pyo.Constraint(
                nodes, hk_block.HKT, T, rule=gen_nameplate_limit
            )

            def power_density_cap_lp(m, node, p):
                return m.total_capacity_kw[node, p] <= (
                    m.max_power_density_kw_per_m2[p] * m.total_swept_area_m2[node, p]
                )

            def area_cap_lp(m, node, p):
                return m.total_swept_area_m2[node, p] <= m.max_swept_area_m2[node, p]

            hk_block.power_density_cap = pyo.Constraint(nodes, hk_block.HKT, rule=power_density_cap_lp)
            hk_block.area_cap = pyo.Constraint(nodes, hk_block.HKT, rule=area_cap_lp)

            if allow_adoption:
                hk_block.hkt_capital_kw = pyo.Expression(
                    expr=sum(
                        hk_block.capital_cost_per_kw[p]
                        * hk_block.capacity_kw_adopted[node, p]
                        * hk_block.amortization_factor
                        for p in hk_block.HKT
                        for node in nodes
                    )
                )
                hk_block.hkt_capital_m2 = pyo.Expression(
                    expr=sum(
                        hk_block.capital_cost_per_m2[p]
                        * hk_block.swept_area_adopted[node, p]
                        * hk_block.amortization_factor
                        for p in hk_block.HKT
                        for node in nodes
                    )
                )
                hk_block.hkt_fixed_om = pyo.Expression(
                    expr=sum(
                        hk_block.fixed_om_per_kw_year[p] * hk_block.capacity_kw_adopted[node, p]
                        + hk_block.fixed_om_per_m2_year[p] * hk_block.swept_area_adopted[node, p]
                        for p in hk_block.HKT
                        for node in nodes
                    )
                )
            else:
                hk_block.hkt_capital_kw = pyo.Expression(expr=0.0)
                hk_block.hkt_capital_m2 = pyo.Expression(expr=0.0)
                hk_block.hkt_fixed_om = pyo.Expression(expr=0.0)

            hk_block.hkt_variable_om = pyo.Expression(
                expr=sum(
                    hk_block.variable_om_per_kwh[p] * hk_block.hkt_generation[node, p, t]
                    for p in hk_block.HKT
                    for node in nodes
                    for t in T
                )
            )
            hk_block.objective_contribution = pyo.Expression(
                expr=(
                    hk_block.hkt_capital_kw
                    + hk_block.hkt_capital_m2
                    + hk_block.hkt_fixed_om
                    + hk_block.hkt_variable_om
                )
            )
            hk_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    hk_block.fixed_om_per_kw_year[p] * hk_block.existing_capacity_kw[node, p]
                    + hk_block.fixed_om_per_m2_year[p] * hk_block.existing_swept_area_m2[node, p]
                    for p in hk_block.HKT
                    for node in nodes
                )
            )

        elif formulation == FORMULATION_HYDROKINETIC_UNIT_MILP:

            def installed_units_expr(m, node, p):
                return m.existing_units[node, p] + (
                    m.units_adopted[node, p] if allow_adoption else 0
                )

            if allow_adoption:
                hk_block.units_adopted = pyo.Var(nodes, hk_block.HKT, within=pyo.NonNegativeIntegers)

            hk_block.installed_units = pyo.Expression(nodes, hk_block.HKT, rule=installed_units_expr)

            def total_swept_from_units(m, node, p):
                return m.installed_units[node, p] * m.unit_swept_area_m2[p]

            def total_kw_from_units(m, node, p):
                return m.installed_units[node, p] * m.unit_capacity_kw[p]

            hk_block.total_swept_area_m2 = pyo.Expression(nodes, hk_block.HKT, rule=total_swept_from_units)
            hk_block.total_capacity_kw = pyo.Expression(nodes, hk_block.HKT, rule=total_kw_from_units)

            def gen_resource_limit_milp(m, node, p, t):
                return m.hkt_generation[node, p, t] <= m.total_swept_area_m2[node, p] * m.yield_kwh_per_m2[p, t]

            def gen_nameplate_limit_milp(m, node, p, t):
                return m.hkt_generation[node, p, t] <= m.total_capacity_kw[node, p] * m.time_step_hours

            hk_block.generation_resource_limit = pyo.Constraint(
                nodes, hk_block.HKT, T, rule=gen_resource_limit_milp
            )
            hk_block.generation_nameplate_limit = pyo.Constraint(
                nodes, hk_block.HKT, T, rule=gen_nameplate_limit_milp
            )

            def units_cap_milp(m, node, p):
                return m.installed_units[node, p] <= m.max_installed_units[node, p]

            hk_block.units_cap = pyo.Constraint(nodes, hk_block.HKT, rule=units_cap_milp)

            if allow_adoption:
                hk_block.hkt_capital_units = pyo.Expression(
                    expr=sum(
                        hk_block.annual_capital_per_unit[p] * hk_block.units_adopted[node, p]
                        for p in hk_block.HKT
                        for node in nodes
                    )
                )
                hk_block.hkt_fixed_om = pyo.Expression(
                    expr=sum(
                        hk_block.fixed_om_per_unit_year_param[p] * hk_block.units_adopted[node, p]
                        for p in hk_block.HKT
                        for node in nodes
                    )
                )
            else:
                hk_block.hkt_capital_units = pyo.Expression(expr=0.0)
                hk_block.hkt_fixed_om = pyo.Expression(expr=0.0)

            hk_block.hkt_variable_om = pyo.Expression(
                expr=sum(
                    hk_block.variable_om_per_kwh[p] * hk_block.hkt_generation[node, p, t]
                    for p in hk_block.HKT
                    for node in nodes
                    for t in T
                )
            )
            hk_block.objective_contribution = pyo.Expression(
                expr=hk_block.hkt_capital_units + hk_block.hkt_fixed_om + hk_block.hkt_variable_om
            )
            hk_block.cost_non_optimizing_annual = pyo.Expression(
                expr=sum(
                    hk_block.fixed_om_per_unit_year_param[p] * hk_block.existing_units[node, p]
                    for p in hk_block.HKT
                    for node in nodes
                )
            )

        hk_block.electricity_source_term = pyo.Expression(
            nodes,
            T,
            rule=lambda m, node, t: sum(m.hkt_generation[node, p, t] for p in m.HKT),
        )

    model.hydrokinetic = pyo.Block(rule=block_rule)
    return model.hydrokinetic
