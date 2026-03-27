"""Per-node utility tariff mapping and billing behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pyomo.environ as pyo
import pytest

import run.build_run_data as brd
from config.case_config import UtilityTariffConfig
from data_loading.loaders.utility_rates.openei_router import ParsedRate
from data_loading.schemas import DataContainer
from model.core import build_model


def _two_node_data() -> DataContainer:
    return DataContainer(
        indices={"time": [0, 1]},
        timeseries={
            "datetime": [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 1, 0)],
            "time_serial": [0, 1],
            "electricity_load__a": [1.0, 1.0],
            "electricity_load__b": [1.0, 1.0],
        },
        static={
            "electricity_load_keys": ["electricity_load__a", "electricity_load__b"],
            "time_step_hours": 1.0,
        },
    )


def test_per_node_energy_prices_are_used():
    data = _two_node_data()
    data.import_prices_by_node = {
        "electricity_load__a": [0.1, 0.2],
        "electricity_load__b": [0.3, 0.4],
    }
    data.utility_rate_by_node = {"electricity_load__a": None, "electricity_load__b": None}

    m = build_model(data, technology_parameters={}, financials={})
    m.utility.grid_import["electricity_load__a", 0].value = 1.0
    m.utility.grid_import["electricity_load__a", 1].value = 2.0
    m.utility.grid_import["electricity_load__b", 0].value = 3.0
    m.utility.grid_import["electricity_load__b", 1].value = 4.0

    # 0.1*1 + 0.2*2 + 0.3*3 + 0.4*4 = 3.0
    assert pyo.value(m.utility.energy_import_cost) == pytest.approx(3.0)


def test_demand_charge_components_only_include_nodes_with_demand_tariffs():
    data = _two_node_data()
    data.import_prices_by_node = {"electricity_load__a": [0.0, 0.0], "electricity_load__b": [0.0, 0.0]}
    data.utility_rate_by_node = {
        "electricity_load__a": ParsedRate(
            rate_type="tou",
            utility="U",
            name="A",
            demand_charges={
                "demand_charge_type": "flat",
                "flat_demand_charge_structure": [[{"rate": 10.0}]],
                "flat_demand_charge_months": [0] * 12,
                "flat_demand_charge_applicable_months": list(range(12)),
            },
        ),
        "electricity_load__b": ParsedRate(rate_type="tou", utility="U", name="B", demand_charges=None),
    }
    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m.utility, "P_flat_y2024_m0")
    assert list(m.utility.P_flat_y2024_m0.keys()) == ["electricity_load__a"]


def test_fixed_charges_are_billed_per_node_reporting_only():
    data = _two_node_data()
    data.import_prices_by_node = {"electricity_load__a": [0.0, 0.0], "electricity_load__b": [0.0, 0.0]}
    common_rate = ParsedRate(
        rate_type="tou",
        utility="U",
        name="FixedOnly",
        customer_fixed_charges={"first_meter": {"amount": 2.0, "units": "$/day"}},
        demand_charges=None,
    )
    data.utility_rate_by_node = {"electricity_load__a": common_rate, "electricity_load__b": common_rate}
    m = build_model(data, technology_parameters={}, financials={})
    # Two nodes, one represented day -> 2 + 2 = 4
    assert pyo.value(m.utility.cost_non_optimizing_annual) == pytest.approx(4.0)


def test_build_run_data_multitariff_default_plus_override(monkeypatch, tmp_path: Path):
    def fake_load_energy_load(_cfg):
        return _two_node_data()

    def fake_load_openei_rate(path, *, item_index=None):
        # Use path stem to produce distinct objects/rates.
        if "com" in str(path).lower():
            return ParsedRate(
                rate_type="tou",
                utility="U",
                name="COM",
                demand_charges={
                    "demand_charge_type": "flat",
                    "flat_demand_charge_structure": [[{"rate": 7.0}]],
                    "flat_demand_charge_months": [0] * 12,
                    "flat_demand_charge_applicable_months": list(range(12)),
                },
            )
        return ParsedRate(rate_type="tou", utility="U", name="RES", demand_charges=None)

    class FakeRaw:
        def __init__(self, prices):
            self.prices = prices

    def fake_load_raw_energy_prices(path, *, price_column=None):
        if "com" in str(path).lower():
            return FakeRaw([0.5, 0.6])
        return FakeRaw([0.1, 0.2])

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    monkeypatch.setattr(brd, "load_openei_rate", fake_load_openei_rate)
    monkeypatch.setattr(brd, "load_raw_energy_prices", fake_load_raw_energy_prices)
    monkeypatch.setattr(brd, "get_import_prices_for_timestamps", lambda source, timestamps: list(source.prices))

    res_rate = tmp_path / "res_rate.json"
    com_rate = tmp_path / "com_rate.json"
    res_price = tmp_path / "res_prices.csv"
    com_price = tmp_path / "com_prices.csv"
    for p in (res_rate, com_rate):
        p.write_text("{}", encoding="utf-8")
    for p in (res_price, com_price):
        p.write_text("price\n0.1\n", encoding="utf-8")

    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        utility_rate_path=None,
        utility_rate_item_index=None,
        energy_price_path=None,
        energy_price_column=None,
        utility_tariffs=[
            UtilityTariffConfig(
                tariff_key="res_default",
                utility_rate_path=res_rate,
                energy_price_path=res_price,
            ),
            UtilityTariffConfig(
                tariff_key="com",
                utility_rate_path=com_rate,
                energy_price_path=com_price,
            ),
        ],
        node_utility_tariff={"electricity_load__b": "com"},
        time_subset=None,
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")

    data = brd.build_run_data(tmp_path, case_cfg)
    assert data.node_utility_tariff_key == {
        "electricity_load__a": "res_default",
        "electricity_load__b": "com",
    }
    assert data.import_prices_by_node["electricity_load__a"] == [0.1, 0.2]
    assert data.import_prices_by_node["electricity_load__b"] == [0.5, 0.6]
    assert data.utility_rate_by_node["electricity_load__a"].demand_charges is None
    assert data.utility_rate_by_node["electricity_load__b"].demand_charges is not None


def test_build_run_data_shares_one_price_vector_per_tariff(monkeypatch, tmp_path: Path):
    """Nodes on the same tariff should reference the same list (memory O(tariffs*T), not O(nodes*T))."""

    def fake_load_energy_load(_cfg):
        return _two_node_data()

    def fake_load_openei_rate(_path, *, item_index=None):
        return ParsedRate(rate_type="tou", utility="U", name="R", demand_charges=None)

    class FakeRaw:
        def __init__(self):
            self.prices = [0.11, 0.22]

    def fake_load_raw_energy_prices(_path, *, price_column=None):
        return FakeRaw()

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    monkeypatch.setattr(brd, "load_openei_rate", fake_load_openei_rate)
    monkeypatch.setattr(brd, "load_raw_energy_prices", fake_load_raw_energy_prices)
    monkeypatch.setattr(brd, "get_import_prices_for_timestamps", lambda source, timestamps: list(source.prices))

    rate = tmp_path / "rate.json"
    price = tmp_path / "prices.csv"
    rate.write_text("{}", encoding="utf-8")
    price.write_text("p\n0\n", encoding="utf-8")

    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        utility_rate_path=None,
        utility_rate_item_index=None,
        energy_price_path=None,
        energy_price_column=None,
        utility_tariffs=[
            UtilityTariffConfig(
                tariff_key="only",
                utility_rate_path=rate,
                energy_price_path=price,
            ),
        ],
        node_utility_tariff=None,
        time_subset=None,
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")

    data = brd.build_run_data(tmp_path, case_cfg)
    a = data.import_prices_by_node["electricity_load__a"]
    b = data.import_prices_by_node["electricity_load__b"]
    assert a is b
    assert a == [0.11, 0.22]


def test_invalid_override_node_or_tariff_key_fails(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _two_node_data())
    monkeypatch.setattr(brd, "load_openei_rate", lambda _p, *, item_index=None: ParsedRate(rate_type="tou", utility="U", name="X"))

    rate = tmp_path / "rate.json"
    rate.write_text("{}", encoding="utf-8")
    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        utility_rate_path=None,
        utility_rate_item_index=None,
        energy_price_path=None,
        energy_price_column=None,
        utility_tariffs=[UtilityTariffConfig(tariff_key="default", utility_rate_path=rate)],
        node_utility_tariff={"bad_node": "default"},
        time_subset=None,
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown node"):
        brd.build_run_data(tmp_path, case_cfg)

    case_cfg.node_utility_tariff = {"electricity_load__a": "missing"}
    with pytest.raises(ValueError, match="unknown tariff_key"):
        brd.build_run_data(tmp_path, case_cfg)

