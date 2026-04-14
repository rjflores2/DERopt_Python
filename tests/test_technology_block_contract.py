"""Technology block interface validation (``model.contracts``)."""

from datetime import datetime

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from model.contracts import (
    TECH_OBJECTIVE_CONTRIBUTION,
    validate_technology_block_interface,
)
from model.core import build_model


def _minimal_model() -> pyo.ConcreteModel:
    m = pyo.ConcreteModel()
    m.T = pyo.Set(initialize=[0], ordered=True)
    m.NODES = pyo.Set(initialize=["electricity_load__x"], ordered=True)
    return m


def test_validate_passes_minimal_block_objective_only():
    m = _minimal_model()
    b = pyo.Block()
    b.objective_contribution = pyo.Expression(expr=0.0)
    validate_technology_block_interface(technology_key="test_tech", block=b, model=m)


def test_validate_passes_indexed_electricity_terms():
    m = _minimal_model()
    m.test_tech = pyo.Block()
    b = m.test_tech
    b.objective_contribution = pyo.Expression(expr=0.0)
    b.cost_non_optimizing_annual = pyo.Expression(expr=0.0)
    b.electricity_source_term = pyo.Expression(
        m.NODES, m.T, rule=lambda mm, n, t: 0.0
    )
    b.electricity_sink_term = pyo.Expression(
        m.NODES, m.T, rule=lambda mm, n, t: 0.0
    )
    validate_technology_block_interface(technology_key="test_tech", block=b, model=m)


def test_validate_passes_indexed_hydrogen_terms():
    m = _minimal_model()
    m.test_tech = pyo.Block()
    b = m.test_tech
    b.objective_contribution = pyo.Expression(expr=0.0)
    b.hydrogen_source_term = pyo.Expression(
        m.NODES, m.T, rule=lambda mm, n, t: 0.0
    )
    b.hydrogen_sink_term = pyo.Expression(
        m.NODES, m.T, rule=lambda mm, n, t: 0.0
    )
    validate_technology_block_interface(technology_key="test_tech", block=b, model=m)


def test_validate_rejects_missing_objective():
    m = _minimal_model()
    b = pyo.Block()
    with pytest.raises(ValueError, match=TECH_OBJECTIVE_CONTRIBUTION):
        validate_technology_block_interface(technology_key="bad_tech", block=b, model=m)


def test_validate_rejects_indexed_objective():
    m = _minimal_model()
    b = pyo.Block()
    b.objective_contribution = pyo.Expression(m.T, rule=lambda mm, t: 1.0)
    with pytest.raises(ValueError, match="scalar"):
        validate_technology_block_interface(technology_key="bad_tech", block=b, model=m)


def test_validate_rejects_scalar_electricity_source():
    m = _minimal_model()
    b = pyo.Block()
    b.objective_contribution = pyo.Expression(expr=0.0)
    b.electricity_source_term = pyo.Expression(expr=0.0)
    with pytest.raises(ValueError, match="electricity_source_term"):
        validate_technology_block_interface(technology_key="bad_tech", block=b, model=m)


def test_validate_rejects_indexed_cost_non_optimizing():
    m = _minimal_model()
    b = pyo.Block()
    b.objective_contribution = pyo.Expression(expr=0.0)
    b.cost_non_optimizing_annual = pyo.Expression(m.T, rule=lambda mm, t: 0.0)
    with pytest.raises(ValueError, match="cost_non_optimizing_annual"):
        validate_technology_block_interface(technology_key="bad_tech", block=b, model=m)


def test_build_model_existing_technologies_still_construct():
    """Regression: full stack build with registry technologies satisfies the contract."""
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "solar_production_keys": ["solar_p"],
            "time_step_hours": 1.0,
        },
    )
    tech = {
        "solar_pv": {},
        "battery_energy_storage": {},
        "diesel_generator": {},
    }
    m = build_model(data, technology_parameters=tech, financials={})
    assert hasattr(m, "solar_pv")
    assert hasattr(m, "battery_energy_storage")
    assert hasattr(m, "diesel_generator")
    assert m.obj.is_constructed()


def test_build_model_raises_when_register_returns_block_not_attached(monkeypatch):
    import technologies

    def bad_register(model, data, *, technology_parameters=None, financials=None):
        return pyo.Block()

    old_registry = list(technologies.REGISTRY)
    monkeypatch.setattr(
        technologies,
        "REGISTRY",
        [("solar_pv", bad_register)] + [x for x in old_registry if x[0] != "solar_pv"],
    )
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "solar_production_keys": ["solar_p"],
            "time_step_hours": 1.0,
        },
    )
    with pytest.raises(ValueError, match="missing"):
        build_model(data, technology_parameters={"solar_pv": {}}, financials={})


def test_build_model_raises_when_register_returns_none_but_block_exists(monkeypatch):
    import technologies

    def bad_register(model, data, *, technology_parameters=None, financials=None):
        b = pyo.Block()
        b.objective_contribution = pyo.Expression(expr=0.0)
        model.solar_pv = b
        return None

    old_registry = list(technologies.REGISTRY)
    monkeypatch.setattr(
        technologies,
        "REGISTRY",
        [("solar_pv", bad_register)] + [x for x in old_registry if x[0] != "solar_pv"],
    )
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "solar_production_keys": ["solar_p"],
            "time_step_hours": 1.0,
        },
    )
    with pytest.raises(ValueError, match="returned None"):
        build_model(data, technology_parameters={"solar_pv": {}}, financials={})


def test_build_model_raises_when_requested_tech_skips_attach_and_returns_none():
    """Requesting a registry tech must yield an attached block; silent skip is an error."""
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "time_step_hours": 1.0,
        },
    )
    with pytest.raises(ValueError, match="was requested in technology_parameters"):
        build_model(data, technology_parameters={"solar_pv": {}}, financials={})


def test_build_model_raises_on_register_return_identity_mismatch(monkeypatch):
    import technologies

    def bad_register(model, data, *, technology_parameters=None, financials=None):
        ret = pyo.Block()
        model.solar_pv = pyo.Block()
        model.solar_pv.objective_contribution = pyo.Expression(expr=0.0)
        return ret

    old_registry = list(technologies.REGISTRY)
    monkeypatch.setattr(
        technologies,
        "REGISTRY",
        [("solar_pv", bad_register)] + [x for x in old_registry if x[0] != "solar_pv"],
    )
    data = DataContainer(
        indices={"time": [0]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0)],
            "time_serial": [0],
            "electricity_load__x": [10.0],
            "solar_p": [0.5],
        },
        static={
            "electricity_load_keys": ["electricity_load__x"],
            "solar_production_keys": ["solar_p"],
            "time_step_hours": 1.0,
        },
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        build_model(data, technology_parameters={"solar_pv": {}}, financials={})
