"""CaseConfig utility_mode: priced vs free grid vs islanded."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import run.build_run_data as brd
from config.case_config import CaseConfig, EnergyLoadFileConfig, UtilityTariffConfig
from data_loading.schemas import DataContainer
from model.core import build_model
from utilities.model_diagnostics import collect_model_diagnostics


def test_priced_grid_without_utility_source_raises(tmp_path: Path, monkeypatch) -> None:
    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0]},
            timeseries={"time_serial": [0], "electricity_load__x": [0.0]},
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        )

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        utility_rate_path=None,
        energy_price_path=None,
        utility_tariffs=None,
        time_subset=None,
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="utility_mode='priced_grid' requires"):
        brd.build_run_data(tmp_path, case_cfg)


def test_free_grid_zero_prices_and_no_free_grid_diagnostic_warning(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0, 1]},
            timeseries={
                "time_serial": [0, 1],
                "electricity_load__x": [0.0, 0.0],
            },
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        )

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        utility_rate_path=None,
        energy_price_path=None,
        utility_tariffs=None,
        time_subset=None,
        utility_mode="free_grid",
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")

    data = brd.build_run_data(tmp_path, case_cfg)
    assert data.import_prices_by_node is not None
    assert data.import_prices_by_node["electricity_load__x"] == [0.0, 0.0]

    model = build_model(data, technology_parameters={}, financials={})
    assert hasattr(model, "utility")
    diag = collect_model_diagnostics(model, data, case_cfg)
    assert not any("Grid imports may be free" in w for w in diag)


def test_islanded_no_utility_block_zero_load_feasible(tmp_path: Path, monkeypatch) -> None:
    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0]},
            timeseries={"time_serial": [0], "electricity_load__x": [0.0]},
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        )

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    case_cfg = CaseConfig(
        case_name="islanded_test",
        energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "loads.csv"),
        utility_mode="islanded",
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")

    data = brd.build_run_data(tmp_path, case_cfg)
    assert data.import_prices_by_node is None
    assert data.utility_rate_by_node is None

    model = build_model(data, technology_parameters={}, financials={})
    assert getattr(model, "utility", None) is None


def test_multi_tariff_with_islanded_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        brd,
        "load_energy_load",
        lambda _cfg: DataContainer(
            indices={"time": [0]},
            timeseries={"time_serial": [0], "electricity_load__x": [0.0]},
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        ),
    )
    rate = tmp_path / "r.json"
    rate.write_text("{}", encoding="utf-8")
    case_cfg = CaseConfig(
        case_name="bad",
        energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "loads.csv"),
        utility_mode="islanded",
        utility_tariffs=[UtilityTariffConfig(tariff_key="a", utility_rate_path=rate)],
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="utility_mode='islanded' cannot be combined"):
        brd.build_run_data(tmp_path, case_cfg)


def test_free_grid_with_paths_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        brd,
        "load_energy_load",
        lambda _cfg: DataContainer(
            indices={"time": [0]},
            timeseries={"time_serial": [0], "electricity_load__x": [0.0]},
            static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
        ),
    )
    case_cfg = CaseConfig(
        case_name="bad",
        energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "loads.csv"),
        utility_mode="free_grid",
        utility_rate_path=tmp_path / "x.json",
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    case_cfg.utility_rate_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="utility_mode='free_grid' cannot be combined"):
        brd.build_run_data(tmp_path, case_cfg)
