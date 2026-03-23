"""
Battery energy storage technology block.

Battery is modeled per node and per time step with:
- State of charge (energy in storage) at each node and time.
- Separate charging and discharging power variables at each node and time.
- An energy balance that links state of charge across time steps.
- Power limits that scale with installed energy capacity (C‑rates).
- An energy‑capacity limit (existing + adopted capacity) at each node.

The block contributes to the system electricity balance via:
- electricity_source_term[node, t] (discharging adds to sources),
- electricity_sink_term[node, t]  (charging adds to sinks),
so it plugs directly into the electricity balance built in model.core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from shared.financials import annualization_factor_debt_equity


# -----------------------------------------------------------------------------
# Default parameters (used when battery_params does not supply overrides)
# -----------------------------------------------------------------------------

DEFAULT_BATTERY_PARAMS = {
    "allow_adoption": True,          # If False, only existing capacity is modeled (no adoption variable).
    # Round‑trip efficiency is split into charge/discharge legs:
    # effective round‑trip = charge_efficiency * discharge_efficiency.
    "charge_efficiency": 0.95,
    "discharge_efficiency": 0.95,
    # Capital and fixed O&M costs per kWh of energy capacity.
    "capital_cost_per_kwh": 400.0,   # $/kWh (one‑time)
    "om_per_kwh_year": 10.0,         # $/kWh‑year O&M
    # Maximum charging/discharging power per kWh of energy capacity (C‑rates).
    # Example: 0.5 means a 2‑hour battery (max power = 0.5 * energy_capacity).
    "max_charge_power_per_kwh": 0.5,
    "max_discharge_power_per_kwh": 0.5,
    # Existing energy capacity by node: {node_key: kWh}.
    "existing_energy_capacity_by_node": None,
    # Optional initial state of charge as a fraction of usable capacity (0..1).
    # If None, we enforce cyclic SOC (end == start) instead of fixing initial SOC.
    "initial_soc_fraction": None,
}


def add_battery_energy_storage_block(
    model: Any,
    data: Any,
    *,
    battery_params: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Build and attach the Battery Energy Storage block to the model.

    Battery is optimized at each node (one per load) and time step.

    Data used:
        - model.T, model.NODES -> time and node sets (created in core from data)
        - battery_params       -> optional overrides for technical/economic params

    Block contents (per node n and time t):
        - Vars:
            energy_state[n, t]          [kWh]  state of charge
            charge_power[n, t]          [kW]   charging power (electricity sink)
            discharge_power[n, t]       [kW]   discharging power (electricity source)
            energy_capacity_adopted[n]  [kWh]  adopted energy capacity (if allow_adoption)
        - Constraints:
            Energy balance over time (cyclic or with fixed initial SOC).
            0 <= energy_state[n, t] <= existing + adopted capacity.
            charge_power and discharge_power limited by C‑rates * capacity.
        - Expressions:
            electricity_source_term[n, t] = discharge_power[n, t]
            electricity_sink_term[n, t]   = charge_power[n, t]
            objective_contribution: annualized capital + O&M on total capacity.
    """
    T = model.T
    NODES = list(model.NODES)

    allow_adoption = (battery_params or {}).get("allow_adoption", True)
    r = _resolve_battery_block_inputs(battery_params, financials, NODES)

    _nodes = model.NODES
    _T = list(T)

    def block_rule(b):
        # Decision variables
        b.energy_state = pyo.Var(_nodes, T, within=pyo.NonNegativeReals)
        b.charge_power = pyo.Var(_nodes, T, within=pyo.NonNegativeReals)
        b.discharge_power = pyo.Var(_nodes, T, within=pyo.NonNegativeReals)

        if allow_adoption:
            # Adoption variable: additional energy capacity at each node [kWh].
            b.energy_capacity_adopted = pyo.Var(_nodes, within=pyo.NonNegativeReals)

            def total_energy_capacity(m, n):
                return r.existing_energy_capacity[n] + m.energy_capacity_adopted[n]
        else:
            # Existing‑only: total capacity is fixed from input data.
            def total_energy_capacity(m, n):
                return r.existing_energy_capacity[n]

        # Total usable energy capacity at each node (existing + adopted).
        b.total_energy_capacity = pyo.Expression(
            _nodes, rule=lambda m, n: total_energy_capacity(m, n)
        )

        # Energy capacity limits: SOC cannot exceed total capacity.
        def energy_capacity_limit_rule(m, n, t):
            return m.energy_state[n, t] <= m.total_energy_capacity[n]

        b.energy_capacity_limit = pyo.Constraint(_nodes, T, rule=energy_capacity_limit_rule)

        # Power limits: charge/discharge bounded by C‑rates * total capacity.
        def charge_power_limit_rule(m, n, t):
            return m.charge_power[n, t] <= (
                r.max_charge_power_per_kwh * m.total_energy_capacity[n]
            )

        def discharge_power_limit_rule(m, n, t):
            return m.discharge_power[n, t] <= (
                r.max_discharge_power_per_kwh * m.total_energy_capacity[n]
            )

        b.charge_power_limit = pyo.Constraint(_nodes, T, rule=charge_power_limit_rule)
        b.discharge_power_limit = pyo.Constraint(_nodes, T, rule=discharge_power_limit_rule)

        # Energy balance: state of charge over time (cyclic by default).
        def energy_balance_rule(m, n, t):
            idx = _T.index(t)
            if idx == 0:
                # Previous time step: last index (cyclic constraint) unless initial SOC is fixed.
                prev_t = _T[-1]
            else:
                prev_t = _T[idx - 1]

            return m.energy_state[n, t] == (
                m.energy_state[n, prev_t]
                + r.charge_efficiency * m.charge_power[n, t]
                - (1.0 / r.discharge_efficiency) * m.discharge_power[n, t]
            )

        b.energy_balance = pyo.Constraint(_nodes, T, rule=energy_balance_rule)

        # Optional initial state‑of‑charge constraint (non‑cyclic if specified).
        if r.initial_soc_fraction is not None and _T:
            t0 = _T[0]

            def initial_soc_rule(m, n):
                return m.energy_state[n, t0] == r.initial_soc_fraction * m.total_energy_capacity[n]

            b.initial_soc = pyo.Constraint(_nodes, rule=initial_soc_rule)

        # Electricity contributions: discharge is a source; charge is a sink.
        b.electricity_source_term = pyo.Expression(
            _nodes, T, rule=lambda m, n, t: m.discharge_power[n, t]
        )
        b.electricity_sink_term = pyo.Expression(
            _nodes, T, rule=lambda m, n, t: m.charge_power[n, t]
        )

        # Cost terms: capital on adopted capacity (if any) + O&M on total capacity.
        if allow_adoption:
            b.objective_contribution = sum(
                r.capital_cost_per_kwh * b.energy_capacity_adopted[n] * r.amortization_factor
                + r.om_per_kwh_year * b.total_energy_capacity[n]
                for n in _nodes
            )
            # Existing‑asset annual cost for reporting (does not affect optimum).
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    r.om_per_kwh_year * r.existing_energy_capacity[n]
                    for n in _nodes
                )
            )
        else:
            # Existing‑only: objective includes O&M on existing capacity only.
            b.objective_contribution = sum(
                r.om_per_kwh_year * r.existing_energy_capacity[n]
                for n in _nodes
            )
            b.cost_existing_annual = pyo.Expression(
                expr=sum(
                    r.om_per_kwh_year * r.existing_energy_capacity[n]
                    for n in _nodes
                )
            )

    model.battery_energy_storage = pyo.Block(rule=block_rule)
    return model.battery_energy_storage


def register(
    model: Any,
    data: Any,
    *,
    technology_parameters: dict[str, Any] | None = None,
    financials: dict[str, Any] | None = None,
) -> pyo.Block:
    """
    Attach the Battery Energy Storage block.

    Called by core from the technology registry. Params are read from
    technology_parameters["battery_energy_storage"].
    """
    battery_params = (technology_parameters or {}).get("battery_energy_storage") or {}
    return add_battery_energy_storage_block(
        model,
        data,
        battery_params=battery_params,
        financials=financials or {},
    )


# -----------------------------------------------------------------------------
# Plumbing (helpers used by add_battery_energy_storage_block)
# -----------------------------------------------------------------------------

@dataclass
class _ResolvedBatteryInputs:
    """All parameter‑derived inputs for the battery block (no time series)."""

    charge_efficiency: float              # Charge leg efficiency (0..1]
    discharge_efficiency: float           # Discharge leg efficiency (0..1]
    capital_cost_per_kwh: float           # $/kWh capital
    om_per_kwh_year: float                # $/kWh‑year fixed O&M
    max_charge_power_per_kwh: float       # kW/kWh (C‑rate) for charging
    max_discharge_power_per_kwh: float    # kW/kWh (C‑rate) for discharging
    existing_energy_capacity: dict[str, float]  # Existing energy capacity at each node [kWh]
    amortization_factor: float            # Capital → annualized cost factor
    initial_soc_fraction: float | None    # Optional SOC fraction for first period


def _resolve_battery_block_inputs(
    battery_params: dict[str, Any] | None,
    financials: dict[str, Any] | None,
    nodes: list[str],
) -> _ResolvedBatteryInputs:
    """
    Merge defaults with user overrides and resolve per‑node battery parameters.

    Returns an object used by add_battery_energy_storage_block to build the Pyomo block.
    """
    params = (battery_params or {}).copy()
    for k, v in DEFAULT_BATTERY_PARAMS.items():
        params.setdefault(k, v)

    # Efficiency checks
    charge_eff = float(params["charge_efficiency"])
    discharge_eff = float(params["discharge_efficiency"])
    if not (0 < charge_eff <= 1) or not (0 < discharge_eff <= 1):
        raise ValueError(
            "battery_energy_storage: charge_efficiency and discharge_efficiency "
            "must each be in (0, 1]."
        )

    # Cost parameters
    capital = float(params["capital_cost_per_kwh"])
    om = float(params["om_per_kwh_year"])
    if capital < 0 or om < 0:
        raise ValueError(
            "battery_energy_storage: capital_cost_per_kwh and om_per_kwh_year must be >= 0."
        )

    # Power (C‑rate) limits
    max_charge_c = float(params["max_charge_power_per_kwh"])
    max_discharge_c = float(params["max_discharge_power_per_kwh"])
    if max_charge_c <= 0 or max_discharge_c <= 0:
        raise ValueError(
            "battery_energy_storage: max_*_power_per_kwh must be > 0 (C-rate)."
        )

    # Existing capacity per node
    existing_raw = params.get("existing_energy_capacity_by_node") or {}
    existing_energy_capacity: dict[str, float] = {}
    for n in nodes:
        val = float(existing_raw.get(n, 0.0))
        if val < 0:
            raise ValueError(
                f"battery_energy_storage: existing_energy_capacity for node {n!r} "
                f"must be >= 0, got {val}."
            )
        existing_energy_capacity[n] = val

    # Financials
    fin = financials or {}
    amortization_factor = annualization_factor_debt_equity(**fin)

    # Optional initial SOC fraction
    initial_soc_fraction = params.get("initial_soc_fraction")
    if initial_soc_fraction is not None:
        initial_soc_fraction = float(initial_soc_fraction)
        if not (0 <= initial_soc_fraction <= 1):
            raise ValueError(
                "battery_energy_storage: initial_soc_fraction must be between 0 and 1."
            )

    return _ResolvedBatteryInputs(
        charge_efficiency=charge_eff,
        discharge_efficiency=discharge_eff,
        capital_cost_per_kwh=capital,
        om_per_kwh_year=om,
        max_charge_power_per_kwh=max_charge_c,
        max_discharge_power_per_kwh=max_discharge_c,
        existing_energy_capacity=existing_energy_capacity,
        amortization_factor=amortization_factor,
        initial_soc_fraction=initial_soc_fraction,
    )
