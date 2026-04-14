"""Hydrogen subsystem: electrolyzer, fuel cell, storage, and core hydrogen balance."""

from datetime import datetime

import pytest

from data_loading.schemas import DataContainer
from model.core import build_model


def _minimal_data(*, n_time: int = 3) -> DataContainer:
    dts = [datetime(2024, 1, 1, i, 0) for i in range(n_time)]
    node = "electricity_load__n"
    return DataContainer(
        indices={"time": list(range(n_time))},
        timeseries={
            "datetime": dts,
            "time_serial": [0.0] * n_time,
            node: [5.0] * n_time,
        },
        static={
            "electricity_load_keys": [node],
            "time_step_hours": 1.0,
        },
        import_prices_by_node={node: [0.0] * n_time},
        utility_rate_by_node={node: None},
    )


def _h2_stack_base(node: str):
    return {
        "pem_fuel_cell": {
            "allow_adoption": False,
            "formulation": "pem_fuel_cell_lp",
            "existing_capacity_kw_by_node": {node: 100.0},
            "capacity_adoption_limit_kw_by_node": {node: 0.0},
            "unit_adoption_limit_by_node": {node: 0},
        },
        "compressed_gas_hydrogen_storage": {
            "allow_adoption": False,
            "existing_energy_capacity_kwh_h2_lhv_by_node": {node: 500.0},
            "compression_kwh_electric_per_kwh_h2_lhv": 0.05,
        },
    }


@pytest.mark.parametrize(
    "formulation",
    [
        "pem_electrolyzer_lp",
        "pem_electrolyzer_binary",
        "pem_electrolyzer_unit_milp",
    ],
)
def test_pem_electrolyzer_builds(formulation: str):
    node = "electricity_load__n"
    params = {
        "allow_adoption": False,
        "formulation": formulation,
        "minimum_loading_fraction": 0.0,
    }
    if formulation == "pem_electrolyzer_unit_milp":
        params["existing_unit_count_by_node"] = {node: 1}
        params["unit_capacity_kw"] = 50.0
        params["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        params["unit_adoption_limit_by_node"] = {node: 0}
    else:
        params["existing_capacity_kw_by_node"] = {node: 50.0}
        params["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        params["unit_adoption_limit_by_node"] = {node: 0}

    data = _minimal_data()
    tech = {**_h2_stack_base(node), "pem_electrolyzer": params}
    m = build_model(data, technology_parameters=tech, financials={})
    assert hasattr(m, "pem_electrolyzer")
    assert hasattr(m, "hydrogen_balance")


@pytest.mark.parametrize(
    "formulation",
    [
        "alkaline_electrolyzer_lp",
        "alkaline_electrolyzer_binary",
        "alkaline_electrolyzer_unit_milp",
    ],
)
def test_alkaline_electrolyzer_builds(formulation: str):
    node = "electricity_load__n"
    params = {
        "allow_adoption": False,
        "formulation": formulation,
        "minimum_loading_fraction": 0.0,
    }
    if formulation == "alkaline_electrolyzer_unit_milp":
        params["existing_unit_count_by_node"] = {node: 1}
        params["unit_capacity_kw"] = 50.0
        params["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        params["unit_adoption_limit_by_node"] = {node: 0}
    else:
        params["existing_capacity_kw_by_node"] = {node: 50.0}
        params["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        params["unit_adoption_limit_by_node"] = {node: 0}

    data = _minimal_data()
    tech = {**_h2_stack_base(node), "alkaline_electrolyzer": params}
    m = build_model(data, technology_parameters=tech, financials={})
    assert hasattr(m, "alkaline_electrolyzer")
    assert hasattr(m, "hydrogen_balance")


@pytest.mark.parametrize(
    "formulation",
    [
        "pem_fuel_cell_lp",
        "pem_fuel_cell_binary",
        "pem_fuel_cell_unit_milp",
    ],
)
def test_pem_fuel_cell_builds(formulation: str):
    node = "electricity_load__n"
    fc = {
        "allow_adoption": False,
        "formulation": formulation,
        "minimum_loading_fraction": 0.0,
    }
    if formulation == "pem_fuel_cell_unit_milp":
        fc["existing_unit_count_by_node"] = {node: 1}
        fc["unit_capacity_kw"] = 50.0
        fc["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        fc["unit_adoption_limit_by_node"] = {node: 0}
    else:
        fc["existing_capacity_kw_by_node"] = {node: 50.0}
        fc["capacity_adoption_limit_kw_by_node"] = {node: 0.0}
        fc["unit_adoption_limit_by_node"] = {node: 0}

    el = {
        "allow_adoption": False,
        "formulation": "pem_electrolyzer_lp",
        "existing_capacity_kw_by_node": {node: 50.0},
        "capacity_adoption_limit_kw_by_node": {node: 0.0},
        "unit_adoption_limit_by_node": {node: 0},
    }
    data = _minimal_data()
    m = build_model(
        data,
        technology_parameters={
            "pem_electrolyzer": el,
            "pem_fuel_cell": fc,
            **_h2_stack_base(node),
        },
        financials={},
    )
    assert hasattr(m, "pem_fuel_cell")
    assert hasattr(m, "hydrogen_balance")


def test_compressed_gas_hydrogen_storage_builds():
    node = "electricity_load__n"
    data = _minimal_data()
    m = build_model(
        data,
        technology_parameters={
            "pem_electrolyzer": {
                "allow_adoption": False,
                "formulation": "pem_electrolyzer_lp",
                "existing_capacity_kw_by_node": {node: 10.0},
                "capacity_adoption_limit_kw_by_node": {node: 0.0},
                "unit_adoption_limit_by_node": {node: 0},
            },
            "pem_fuel_cell": _h2_stack_base(node)["pem_fuel_cell"],
            "compressed_gas_hydrogen_storage": _h2_stack_base(node)["compressed_gas_hydrogen_storage"],
        },
        financials={},
    )
    assert hasattr(m, "compressed_gas_hydrogen_storage")
    assert hasattr(m, "hydrogen_balance")


def test_model_hydrogen_balance_present_without_h2_technologies():
    data = _minimal_data()
    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m, "hydrogen_sources")
    assert hasattr(m, "hydrogen_sinks")
    assert hasattr(m, "hydrogen_balance")
