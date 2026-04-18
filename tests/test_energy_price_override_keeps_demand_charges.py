"""Raw energy price override should still allow OpenEI demand charges."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import run.build_run_data as brd
from data_loading.schemas import DataContainer
from data_loading.loaders.utility_rates.openei_router import ParsedRate
from model.core import build_model


def test_raw_energy_prices_override_openei_energy_keep_demand_charges(monkeypatch, tmp_path: Path):
    # Fake load container with datetime (so demand-charge month/tier logic can work)
    from datetime import datetime

    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0]},
            timeseries={
                "datetime": [datetime(2024, 1, 1, 0, 0)],
                "time_serial": [0],
                "electricity_load__x": [1.0],
            },
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        )

    def fake_load_openei_rate(_path, *, item_index=None):
        return ParsedRate(
            rate_type="tou",
            utility="X",
            name="Y",
            payload={"import_prices_12x24_weekday": [0.0] * 288, "import_prices_12x24_weekend": [0.0] * 288},
            demand_charges={
                "demand_charge_type": "flat",
                "flat_demand_charge_structure": [[{"rate": 10.0}]],
                "flat_demand_charge_months": [0] * 12,
                "flat_demand_charge_applicable_months": list(range(12)),
            },
        )

    class FakeRaw:
        def __init__(self):
            self.prices = [0.123]

    def fake_load_raw_energy_prices(_path, *, price_column=None):
        return FakeRaw()

    def fake_get_import_prices_for_timestamps(source, timestamps):
        # If raw override is used, this should be called with FakeRaw and return its price list.
        if isinstance(source, FakeRaw):
            return list(source.prices)
        raise AssertionError("OpenEI TOU energy prices should not be used when raw prices are provided.")

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    monkeypatch.setattr(brd, "load_openei_rate", fake_load_openei_rate)
    monkeypatch.setattr(brd, "load_raw_energy_prices", fake_load_raw_energy_prices)
    monkeypatch.setattr(brd, "get_import_prices_for_timestamps", fake_get_import_prices_for_timestamps)

    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        energy_price_path=tmp_path / "prices.csv",
        energy_price_column=None,
        utility_rate_path=tmp_path / "rate.json",
        utility_rate_item_index=None,
        time_subset=None,
    )

    # placeholders so build_run_data path checks pass
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    case_cfg.energy_price_path.write_text("price\n0.123\n", encoding="utf-8")
    case_cfg.utility_rate_path.write_text("{}", encoding="utf-8")

    data = brd.build_run_data(tmp_path, case_cfg)
    assert data.import_prices_by_node is not None
    assert data.import_prices_by_node["electricity_load__x"] == [0.123]
    assert data.utility_rate_by_node is not None
    assert data.utility_rate_by_node["electricity_load__x"] is not None
    assert data.utility_rate_by_node["electricity_load__x"].demand_charges is not None

    # End-to-end model path: mixed-source inputs should still build demand-charge components.
    m = build_model(data, technology_parameters={}, financials={})
    assert hasattr(m, "utility")
    assert (2024, 0, "electricity_load__x") in m.utility.P_flat


def test_raw_prices_plus_demand_charges_without_datetime_fails_early(monkeypatch, tmp_path: Path):
    # No timeseries["datetime"] -> must fail in build_run_data (not deferred to model build).
    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0]},
            timeseries={
                "time_serial": [0],
                "electricity_load__x": [1.0],
            },
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        )

    def fake_load_openei_rate(_path, *, item_index=None):
        return ParsedRate(
            rate_type="tou",
            utility="X",
            name="Y",
            payload={"import_prices_12x24_weekday": [0.0] * 288, "import_prices_12x24_weekend": [0.0] * 288},
            demand_charges={
                "demand_charge_type": "flat",
                "flat_demand_charge_structure": [[{"rate": 10.0}]],
                "flat_demand_charge_months": [0] * 12,
                "flat_demand_charge_applicable_months": list(range(12)),
            },
        )

    class FakeRaw:
        def __init__(self):
            self.prices = [0.123]

    def fake_load_raw_energy_prices(_path, *, price_column=None):
        return FakeRaw()

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    monkeypatch.setattr(brd, "load_openei_rate", fake_load_openei_rate)
    monkeypatch.setattr(brd, "load_raw_energy_prices", fake_load_raw_energy_prices)

    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        energy_price_path=tmp_path / "prices.csv",
        energy_price_column=None,
        utility_rate_path=tmp_path / "rate.json",
        utility_rate_item_index=None,
        time_subset=None,
    )

    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    case_cfg.energy_price_path.write_text("price\n0.123\n", encoding="utf-8")
    case_cfg.utility_rate_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="includes demand charges.*no timeseries\\['datetime'\\]"):
        brd.build_run_data(tmp_path, case_cfg)

