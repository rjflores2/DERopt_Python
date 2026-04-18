"""Sub-hourly unit-consistency tests.

Verify that capacity (kW) constraints and SOC/balance expressions scale correctly when
``data.static["time_step_hours"]`` differs from 1.0. All flow variables contributing to
the electricity balance are kWh-per-timestep; capacity (kW) constraints multiply by
``model.time_step_hours`` to bound the flow.

Each test builds a model at two time-step resolutions (dt=1.0 and dt=0.5) and checks
that the bounds on flow variables scale linearly with dt.
"""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.core import build_model


def _minimal_data(dt_hours: float, n_time: int = 2) -> DataContainer:
    """One node, ``n_time`` intervals, constant zero load; dt_hours is the only thing that changes."""
    dts = [datetime(2024, 1, 1, 0, 0)] * n_time
    return DataContainer(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            "electricity_load__n": [0.0] * n_time,
        },
        static={
            "electricity_load_keys": ["electricity_load__n"],
            "time_step_hours": dt_hours,
        },
        import_prices_by_node={"electricity_load__n": [0.0] * n_time},
        utility_rate_by_node={"electricity_load__n": None},
    )


# ---------------------------------------------------------------------------
# model.core: time_step_hours exposure
# ---------------------------------------------------------------------------

def test_model_time_step_hours_param_present_and_correct():
    m = build_model(_minimal_data(dt_hours=0.25), technology_parameters={}, financials={})
    assert hasattr(m, "time_step_hours")
    assert pyo.value(m.time_step_hours) == pytest.approx(0.25)


def test_model_time_step_hours_rejects_nonpositive():
    """Negative/zero time_step_hours should fail fast."""
    data = _minimal_data(dt_hours=0.0)
    with pytest.raises(ValueError, match="time_step_hours"):
        build_model(data, technology_parameters={}, financials={})


# ---------------------------------------------------------------------------
# Diesel: generation <= installed_kw * time_step_hours
# ---------------------------------------------------------------------------

def _build_diesel(dt_hours: float) -> pyo.ConcreteModel:
    return build_model(
        _minimal_data(dt_hours=dt_hours),
        technology_parameters={
            "diesel_generator": {
                "allow_adoption": False,
                "formulation": "diesel_lp",
                "existing_capacity_by_node": {"electricity_load__n": 10.0},
            }
        },
        financials={},
    )


def _constraint_rhs_when_var_zero(model: pyo.ConcreteModel, con, flow_var) -> float:
    """For a constraint ``flow <= expr``, return ``expr`` by zeroing the flow and
    evaluating ``expr - flow``. Works regardless of Pyomo's canonical representation.
    """
    flow_var.value = 0.0
    # body = flow - expr  OR  body = expr - flow depending on canonical form.
    # We know when flow=0, |body| = expr. Sign handled below via abs.
    return abs(float(pyo.value(con.body)))


def test_diesel_lp_capacity_constraint_scales_with_dt():
    """At dt=1 the bound is 10 kWh/period; at dt=0.5 the bound is 5 kWh/period."""
    for dt, expected_bound in [(1.0, 10.0), (0.5, 5.0)]:
        m = _build_diesel(dt_hours=dt)
        b = m.diesel_generator
        con = b.generation_limits["electricity_load__n", 0]
        rhs = _constraint_rhs_when_var_zero(
            m, con, b.diesel_generation["electricity_load__n", 0]
        )
        assert rhs == pytest.approx(expected_bound)


def test_diesel_variable_cost_correct_at_sub_hourly():
    """Variable cost = $/kWh * (kWh/timestep flow); should NOT gain an extra dt factor."""
    m = _build_diesel(dt_hours=0.5)
    b = m.diesel_generator
    # Pin diesel_generation to 4 kWh at t=0 (i.e. 8 kW average over a half-hour), 0 elsewhere
    b.diesel_generation["electricity_load__n", 0].value = 4.0
    b.diesel_generation["electricity_load__n", 1].value = 0.0
    # cost = variable_om_per_kwh * 4 kWh (not * 4 * 0.5)
    expected = float(pyo.value(b.variable_om_per_kwh)) * 4.0
    assert pyo.value(b.diesel_variable_om_cost) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Battery: charge/discharge limit = C_rate * capacity_kwh * time_step_hours
# ---------------------------------------------------------------------------

def _build_battery(dt_hours: float) -> pyo.ConcreteModel:
    return build_model(
        _minimal_data(dt_hours=dt_hours),
        technology_parameters={
            "battery_energy_storage": {
                "allow_adoption": False,
                "max_charge_power_per_kwh": 0.5,
                "max_discharge_power_per_kwh": 0.5,
                "existing_energy_capacity_by_node": {"electricity_load__n": 100.0},
            }
        },
        financials={},
    )


def test_battery_c_rate_constraint_scales_with_dt():
    """At dt=1 the bound is 0.5*100 = 50 kWh/period; at dt=0.5 it is 25 kWh/period."""
    for dt, expected in [(1.0, 50.0), (0.5, 25.0)]:
        m = _build_battery(dt_hours=dt)
        b = m.battery_energy_storage
        rhs_c = _constraint_rhs_when_var_zero(
            m,
            b.charge_power_limit["electricity_load__n", 0],
            b.charge_power["electricity_load__n", 0],
        )
        rhs_d = _constraint_rhs_when_var_zero(
            m,
            b.discharge_power_limit["electricity_load__n", 0],
            b.discharge_power["electricity_load__n", 0],
        )
        assert rhs_c == pytest.approx(expected)
        assert rhs_d == pytest.approx(expected)


def test_battery_soc_update_no_dt_multiplier():
    """SOC update uses charge_power/discharge_power directly (they're kWh/timestep already)."""
    m = _build_battery(dt_hours=0.5)
    b = m.battery_energy_storage
    # Pin: soc[t-1]=50, charge_power[t]=2, discharge_power[t]=1, eff=1 for simplicity
    # (real efficiency comes from params; we use them symbolically via evaluation)
    b.state_of_charge["electricity_load__n", 0].value = 0.0
    b.state_of_charge["electricity_load__n", 1].value = 0.0
    b.charge_power["electricity_load__n", 1].value = 2.0
    b.discharge_power["electricity_load__n", 1].value = 1.0
    ec = float(pyo.value(b.charge_efficiency))
    ed = float(pyo.value(b.discharge_efficiency))
    ret = float(pyo.value(b.state_of_charge_retention))
    # body = soc[t] - (ret * soc[t-1] + ec * charge - discharge / ed)
    con = b.energy_balance["electricity_load__n", 1]
    body = pyo.value(con.body)
    # soc[t]=0, soc[t-1]=0 → body = 0 - (0 + ec*2 - 1/ed) = -(ec*2 - 1/ed)
    expected_body = 0.0 - (0.0 + ec * 2.0 - 1.0 / ed)
    assert body == pytest.approx(expected_body)


# ---------------------------------------------------------------------------
# Flow battery: charge/discharge <= total_power_capacity * time_step_hours
# ---------------------------------------------------------------------------

def test_flow_battery_power_cap_scales_with_dt():
    for dt, expected in [(1.0, 50.0), (0.5, 25.0)]:
        m = build_model(
            _minimal_data(dt_hours=dt),
            technology_parameters={
                "flow_battery_energy_storage": {
                    "allow_adoption": False,
                    "existing_energy_capacity_by_node": {"electricity_load__n": 1000.0},
                    "existing_power_capacity_by_node": {"electricity_load__n": 50.0},
                }
            },
            financials={},
        )
        b = m.flow_battery_energy_storage
        rhs_c = _constraint_rhs_when_var_zero(
            m,
            b.charge_power_limit["electricity_load__n", 0],
            b.charge_power["electricity_load__n", 0],
        )
        rhs_d = _constraint_rhs_when_var_zero(
            m,
            b.discharge_power_limit["electricity_load__n", 0],
            b.discharge_power["electricity_load__n", 0],
        )
        assert rhs_c == pytest.approx(expected)
        assert rhs_d == pytest.approx(expected)


# ---------------------------------------------------------------------------
# H2 storage: hydrogen_charge_flow <= C_rate * capacity_kwh * time_step_hours
# ---------------------------------------------------------------------------

def test_h2_storage_c_rate_scales_with_dt():
    """At dt=1 the bound is 0.5*200 = 100 kWh-H2/period; at dt=0.5 it is 50 kWh-H2/period."""
    for dt, expected in [(1.0, 100.0), (0.5, 50.0)]:
        m = build_model(
            _minimal_data(dt_hours=dt),
            technology_parameters={
                "compressed_gas_hydrogen_storage": {
                    "allow_adoption": False,
                    "max_hydrogen_charge_per_kwh_capacity": 0.5,
                    "max_hydrogen_discharge_per_kwh_capacity": 0.5,
                    "existing_energy_capacity_kwh_h2_lhv_by_node": {"electricity_load__n": 200.0},
                }
            },
            financials={},
        )
        b = m.compressed_gas_hydrogen_storage
        rhs_c = _constraint_rhs_when_var_zero(
            m,
            b.hydrogen_charge_limit["electricity_load__n", 0],
            b.hydrogen_charge_flow["electricity_load__n", 0],
        )
        rhs_d = _constraint_rhs_when_var_zero(
            m,
            b.hydrogen_discharge_limit["electricity_load__n", 0],
            b.hydrogen_discharge_flow["electricity_load__n", 0],
        )
        assert rhs_c == pytest.approx(expected)
        assert rhs_d == pytest.approx(expected)
