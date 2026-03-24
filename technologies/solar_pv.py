"""
Solar PV technology block.

Solar is modeled per node and per profile:
- Nodes: one per load (data.static["electricity_load_keys"]). Multi-node cases have
  multiple nodes; single-node has one. Each node can host its own solar capacity.
- Profiles: fixed, 1-D tracking, etc. (data.static["solar_production_keys"]). Each
  profile has its own capacity and generation at each node.

Decision variables: solar_capacity_adopted[node, profile], solar_generation[node, profile, t].
Example: 3 nodes × 2 technologies = 6 capacity variables, each with its own constraints.
Solar potential is the same profile time series at every node (no per-node irradiance yet).

Keep this block simple and well commented—technology components are a main place
for manual programming and must stay transparent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from shared.financials import annualization_factor_debt_equity


# -----------------------------------------------------------------------------
# Default parameters (used when solar_pv_params does not supply overrides)
# -----------------------------------------------------------------------------

DEFAULT_SOLAR_PV_PARAMS = {
    "allow_adoption": True,              # If False, only existing capacity is modeled (no adoption variable); default True.
    "efficiency": 0.2,                  # Solar efficiency (output / ~1 kW/m² input); 0..1. Area proxy = capacity/efficiency.
    "capital_cost_per_kw": 1500.0,      # $/kW (one-time)
    "om_per_kw_year": 20.0,             # $/kW-year O&M
    # Area and existing capacity are per (node, profile). Defaults: no area limit, 0 existing.
    # Area limits come from max_capacity_area_by_node_and_profile in solar_pv_params:
    #   {(node_key, profile_key): area} or {node_key: {profile_key: area}}.
    "max_capacity_area_by_node_and_profile": None,
    # Existing capacity comes from existing_solar_capacity_by_node_and_profile:
    #   {(node_key, profile_key): kW} or {node_key: {profile_key: kW}}.
    "existing_solar_capacity_by_node_and_profile": None,
    # Existing fleet only: optional annual capital recovery ($/kW-yr × existing kW), not applied to adopted capacity.
    # None = no recovery charge. Override per profile via params_by_profile.
    "existing_capital_recovery_per_kw_year": None,
    # If True (and existing_capital_recovery_per_kw_year is None), use same annualized capital as marginal
    # new build: capital_cost_per_kw × amortization_factor from case financials.
    "use_marginal_capital_for_existing_recovery": False,
}

# Per-profile overrides: set solar_pv_params["params_by_profile"] to a list in the same
# order as data.static["solar_production_keys"] (first entry = first profile, etc.).
# See README "Technology parameters (solar)" for an example.
# Existing capacity is per (node, profile); use existing_solar_capacity_by_node_and_profile.


# -----------------------------------------------------------------------------
# Block builder (main entry)
# -----------------------------------------------------------------------------

def add_solar_pv_block(
    model: Any,
    data: Any,
    *,
    solar_pv_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Solar PV block (one node index per load bus, one profile index
    per solar resource column). The same solar potential time series is used at every node.

    1. Data and other inputs

       - ``model.T``                                -> time periods (from ``model.core``) -> time index
       - ``model.NODES``                            -> node keys (from ``model.core``, same as
         ``data.static["electricity_load_keys"]``)  -> node index indicating the locations where solar PV can be installed and/or operated
       - ``data.static["solar_production_keys"]``:  -> ordered list of solar profile keys 
       - ``data.timeseries[profile]``               -> solar resource potential tiomseries data (kWh/kW installed capacity)
       - ``solar_pv_params``                        -> user options; merged with defaults in
       - ``financials``                             -> financial information and functions used to amortized capital / O&M costs

    2. Variables (Pyomo ``Var``)
       - ``solar_generation[node, profile, t]``     -> kWh generated in period ``t`` (always).
       - ``solar_capacity_adopted[node, profile]``  -> additional kW to install, only if allow_adotion is True

    3. Parameters (Pyomo ``Param``, fixed once built)
       - ``solar_potential[profile, t]``            -> from data timeseries.
       - ``efficiency[profile]``, ``                -> Solar PV system efficiency from solar_pv_params / params_by_profile
       - ``capital_cost_per_kw[profile]``           -> Solar PV capital cost ($/kW installed) from solar_pv_params / params_by_profile 
       - ``om_per_kw_year[profile]``:               -> Solar PV fixed O&M cost ($/kW installed*year) from solar_pv_params / params_by_profile 
       - ``existing_solar_capacity[node, profile]`` -> Potentially existing solar capacity at each node (kW)
       -  ``max_capacity_area[node, profile]``      -> Maximum allowable area for solar PV installation in m² , defined on ``AREA_LIMIT_INDEX`` (only for pairs listed in ``max_capacity_area_by_node_and_profile``).

    4. Contribution to electricity sources - ``electricity_source_term[node, t]``
       - ``solar_generation[node, p, t]``

    5. Contribution to the cost function  - ``objective``
       - solar_capacity_adopted   -> annualized capital on adopted kW ($/kW), fixed O&M on adopted kW ($/kW*year)
       - existing solar           -> If there is existing solar, we add O&M and remaining capital payments

    6. Constraints
       - ``generation_limits_rule``                      ->  solar_generation <= (solar_capacity_adopted + existing_solar_capacity)*solar_potential`` 
       - ``generation_limits_rule_existing_only``   ->  (existing + adopted) / efficiency <= max_capacity_area
    """
    T = model.T
    NODES = list(model.NODES)

    solar_keys = data.static.get("solar_production_keys") or []
    if not solar_keys:
        raise ValueError("solar_pv block requires data.static['solar_production_keys'] (load solar data first)")
    SOLAR = list(solar_keys)
    production_by_profile = {key: list(data.timeseries[key]) for key in SOLAR}

    r = _resolve_solar_block_inputs(solar_pv_params, financials, NODES, SOLAR)
    allow_adoption = (solar_pv_params or {}).get("allow_adoption", True)
    _nodes = model.NODES

    def block_rule(b):
        b.SOLAR = pyo.Set(initialize=SOLAR, ordered=True)

        b.solar_potential = pyo.Param(
            b.SOLAR, T,
            initialize={(p, t): production_by_profile[p][t] for p in SOLAR for t in T},
            within=pyo.NonNegativeReals,
            mutable=True,
        )
        b.efficiency = pyo.Param(
            b.SOLAR,
            initialize={p: r.efficiency_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.capital_cost_per_kw = pyo.Param(
            b.SOLAR,
            initialize={p: r.capital_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.om_per_kw_year = pyo.Param(
            b.SOLAR,
            initialize={p: r.om_list[i] for i, p in enumerate(SOLAR)},
            within=pyo.NonNegativeReals, mutable=True,
        )
        b.existing_solar_capacity = pyo.Param(
            _nodes, b.SOLAR,
            initialize=r.existing_init,
            within=pyo.NonNegativeReals, mutable=True,
        )
        if r.has_area_limits:
            # User gave at least one (node, profile) -> max area (m2).
            b.AREA_LIMIT_INDEX = pyo.Set(
                dimen=2, initialize=r.area_index, ordered=True
            )
            b.max_capacity_area = pyo.Param(
                b.AREA_LIMIT_INDEX,
                initialize=r.max_capacity_area_by_node_profile,
                within=pyo.NonNegativeReals,
                mutable=True,
            )

        b.solar_generation = pyo.Var(_nodes, b.SOLAR, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            b.solar_capacity_adopted = pyo.Var(_nodes, b.SOLAR, within=pyo.NonNegativeReals)

            def generation_limits_rule(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                    * m.solar_potential[profile, t]
                )
            b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule)

            if r.has_area_limits:
                def capacity_area_cap_rule(m, node, profile):
                    return (
                        (m.existing_solar_capacity[node, profile] + m.solar_capacity_adopted[node, profile])
                        / m.efficiency[profile]
                    ) <= m.max_capacity_area[node, profile]
                b.capacity_area_cap = pyo.Constraint(
                    b.AREA_LIMIT_INDEX, rule=capacity_area_cap_rule
                )

            # Objective: annualized capital on adopted kW; O&M on (existing + adopted);
            # optional capital recovery on existing kW only (see solar_pv_params).
            b.objective_contribution = sum(
                b.capital_cost_per_kw[p] * b.solar_capacity_adopted[n, p] * r.amortization_factor
                + b.om_per_kw_year[p] * (b.existing_solar_capacity[n, p] + b.solar_capacity_adopted[n, p])
                + r.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                for i, p in enumerate(b.SOLAR)
                for n in _nodes
            )
            # Slice of annual cost attributable to existing assets (for reporting).
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    + r.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                    for i, p in enumerate(b.SOLAR)
                    for n in _nodes
                )
            )
        else:
            # No new build: existing kW only; no area caps in this mode.
            def generation_limits_rule_existing_only(m, node, profile, t):
                return m.solar_generation[node, profile, t] <= (
                    m.existing_solar_capacity[node, profile] * m.solar_potential[profile, t]
                )
            b.generation_limits = pyo.Constraint(_nodes, b.SOLAR, T, rule=generation_limits_rule_existing_only)

            b.objective_contribution = sum(
                b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                + r.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                for i, p in enumerate(b.SOLAR)
                for n in _nodes
            )
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    b.om_per_kw_year[p] * b.existing_solar_capacity[n, p]
                    + r.existing_cap_recovery_per_kw[i] * b.existing_solar_capacity[n, p]
                    for i, p in enumerate(b.SOLAR)
                    for n in _nodes
                )
            )

        b.electricity_source_term = pyo.Expression(
            _nodes, T,
            rule=lambda m, n, t: sum(m.solar_generation[n, p, t] for p in m.SOLAR),
        )

    model.solar_pv = pyo.Block(rule=block_rule)
    return model.solar_pv


def register(
    model: Any,
    data: Any,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block | None:
    """
    Attach the Solar PV block if solar data is present; otherwise do nothing.

    Called by core from the technology registry. Params are read from
    ``technology_parameters["solar_pv"]``. Returns the block if attached, None otherwise.
    """
    if not data.static.get("solar_production_keys"):
        return None
    solar_pv_params = (technology_parameters or {}).get("solar_pv") or {}
    return add_solar_pv_block(
        model,
        data,
        solar_pv_params=solar_pv_params,
        financials=financials or {},
    )


# -----------------------------------------------------------------------------
# Plumbing (helpers used by add_solar_pv_block)
# -----------------------------------------------------------------------------

def _params_per_profile(solar_keys: list, global_params: dict) -> tuple[list, list, list]:
    """
    Turn user config into per-profile parameter lists (same order as ``solar_keys``).

    Each solar profile (e.g. fixed tilt vs tracking) can have its own efficiency,
    capital cost, and O&M. ``global_params`` is ``solar_pv_params`` already merged
    with ``DEFAULT_SOLAR_PV_PARAMS``.

    Per-profile overrides live in ``global_params["params_by_profile"]``:

    - ``None`` or missing: use global values for every profile.
    - Dict keyed by profile string: overrides by name
      (e.g. ``"solar_production__fixed_kw_kw"``).
    - List aligned with ``solar_keys``: overrides by position (first entry = first profile).

    Returns
        ``(efficiency_list, capital_list, om_list)``, each length ``len(solar_keys)``.

    Existing capacity is **not** handled here; it is per ``(node, profile)`` via
    ``existing_solar_capacity_by_node_and_profile`` in the block builder.
    """
    by_profile = global_params.get("params_by_profile")
    efficiency_list: list[float] = []
    capital_list: list[float] = []
    om_list: list[float] = []

    for i, key in enumerate(solar_keys):
        if by_profile is None:
            overrides = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(key) or {}).copy()
        elif isinstance(by_profile, list) and i < len(by_profile):
            overrides = (by_profile[i] or {}).copy()
        else:
            overrides = {}

        p = {**global_params, **overrides}
        efficiency_list.append(float(p["efficiency"]))
        capital_list.append(float(p["capital_cost_per_kw"]))
        om_list.append(float(p["om_per_kw_year"]))

    return efficiency_list, capital_list, om_list


def _existing_capital_recovery_per_kw_list(
    solar_keys: list[str],
    global_params: dict[str, Any],
    capital_list: list[float],
    amortization_factor: float,
) -> list[float]:
    """
    Per profile: annual capital recovery on *existing* kW only ($/kW-yr).

    Precedence for each profile (after merging global_params with params_by_profile entry):
    1. If existing_capital_recovery_per_kw_year is not None, use that value.
    2. Else if use_marginal_capital_for_existing_recovery is True, use
       capital_cost_per_kw * amortization_factor (same annualized capital as new adoption).
    3. Else 0.
    """
    by_profile = global_params.get("params_by_profile")
    out: list[float] = []
    for i, key in enumerate(solar_keys):
        if by_profile is None:
            overrides: dict[str, Any] = {}
        elif isinstance(by_profile, dict):
            overrides = (by_profile.get(key) or {}).copy()
        elif isinstance(by_profile, list) and i < len(by_profile):
            overrides = (by_profile[i] or {}).copy()
        else:
            overrides = {}
        merged = {**global_params, **overrides}
        explicit = merged.get("existing_capital_recovery_per_kw_year")
        use_marginal = bool(merged.get("use_marginal_capital_for_existing_recovery", False))
        cap_kw = capital_list[i]
        if explicit is not None:
            val = float(explicit)
        elif use_marginal:
            val = cap_kw * amortization_factor
        else:
            val = 0.0
        profile_label = key if i < len(solar_keys) else f"profile index {i}"
        if val < 0:
            raise ValueError(
                f"solar_pv: existing capital recovery for {profile_label!r} must be >= 0, got {val}. "
                "Check existing_capital_recovery_per_kw_year and use_marginal_capital_for_existing_recovery."
            )
        out.append(val)
    return out


def _validate_solar_params(
    solar_keys: list[str],
    efficiency_list: list[float],
    capital_list: list[float],
    om_list: list[float],
) -> None:
    """Raise ValueError if any solar_pv parameter is invalid (e.g. efficiency 0 or negative)."""
    for i, (eff, cap, om) in enumerate(zip(efficiency_list, capital_list, om_list)):
        profile_label = solar_keys[i] if i < len(solar_keys) else f"profile index {i}"
        if eff <= 0 or eff > 1:
            raise ValueError(
                f"solar_pv: efficiency for {profile_label!r} must be in (0, 1], got {eff}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )
        if cap < 0:
            raise ValueError(
                f"solar_pv: capital_cost_per_kw for {profile_label!r} must be >= 0, got {cap}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )
        if om < 0:
            raise ValueError(
                f"solar_pv: om_per_kw_year for {profile_label!r} must be >= 0, got {om}. "
                "Check technology_parameters['solar_pv'] and params_by_profile."
            )


def _resolve_existing_capacity(
    nodes: list[str],
    solar: list[str],
    params: dict[str, Any],
) -> dict[tuple[str, str], float]:
    """
    Build existing_solar_capacity initializer: (node, profile) -> kW.
    Default 0 everywhere; non-zero only from params["existing_solar_capacity_by_node_and_profile"]
    (nested {node: {profile: kW}} or flat {(node, profile): kW}).
    """
    by_node_profile = params.get("existing_solar_capacity_by_node_and_profile") or {}
    out: dict[tuple[str, str], float] = {}
    for n in nodes:
        for p in solar:
            val = 0.0
            if isinstance(by_node_profile.get(n), dict):
                val = float(by_node_profile[n].get(p, 0.0))
            elif (n, p) in by_node_profile:
                val = float(by_node_profile[(n, p)])
            if val < 0:
                raise ValueError(
                    f"solar_pv: existing_solar_capacity for (node={n!r}, profile={p!r}) must be >= 0, got {val}. "
                    "Check existing_solar_capacity_by_node_and_profile in technology_parameters['solar_pv']."
                )
            out[(n, p)] = val
    return out


@dataclass
class _ResolvedSolarInputs:
    """Parameter-derived inputs for the solar block (no time series)."""

    efficiency_list: list[float]
    capital_list: list[float]
    om_list: list[float]
    existing_cap_recovery_per_kw: list[float]  # $/kW-yr on existing kW, per profile
    existing_init: dict[tuple[str, str], float]
    has_area_limits: bool
    area_index: list[tuple[str, str]]
    max_capacity_area_by_node_profile: dict[tuple[str, str], float]
    amortization_factor: float


def _resolve_solar_block_inputs(
    solar_pv_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
    solar: list[str],
) -> _ResolvedSolarInputs:
    """
    Merge defaults with user overrides; resolve per-profile and per-node parameters.
    Returned object is consumed by ``add_solar_pv_block``.
    """
    params = (solar_pv_params or {}).copy()
    for k, v in DEFAULT_SOLAR_PV_PARAMS.items():
        params.setdefault(k, v)

    efficiency_list, capital_list, om_list = _params_per_profile(solar, params)
    _validate_solar_params(solar, efficiency_list, capital_list, om_list)

    fin = financials or {}
    amortization_factor = annualization_factor_debt_equity(**fin)

    existing_cap_recovery_per_kw = _existing_capital_recovery_per_kw_list(
        solar, params, capital_list, amortization_factor
    )

    area_raw = params.get("max_capacity_area_by_node_and_profile") or {}
    area_index: list[tuple[str, str]] = []
    max_capacity_area_by_node_profile: dict[tuple[str, str], float] = {}
    for n in nodes:
        for p in solar:
            val = None
            if isinstance(area_raw.get(n), dict):
                val = area_raw[n].get(p)
            elif (n, p) in area_raw:
                val = area_raw[(n, p)]
            if val is not None:
                area_val = float(val)
                if area_val <= 0:
                    raise ValueError(
                        f"solar_pv: max_capacity_area for (node={n!r}, profile={p!r}) must be > 0, got {val}. "
                        "Check max_capacity_area_by_node_and_profile in technology_parameters['solar_pv']."
                    )
                area_index.append((n, p))
                max_capacity_area_by_node_profile[(n, p)] = area_val

    has_area_limits = bool(area_index)

    existing_init = _resolve_existing_capacity(nodes, solar, params)

    return _ResolvedSolarInputs(
        efficiency_list=efficiency_list,
        capital_list=capital_list,
        om_list=om_list,
        existing_cap_recovery_per_kw=existing_cap_recovery_per_kw,
        existing_init=existing_init,
        has_area_limits=has_area_limits,
        area_index=area_index,
        max_capacity_area_by_node_profile=max_capacity_area_by_node_profile,
        amortization_factor=amortization_factor,
    )
