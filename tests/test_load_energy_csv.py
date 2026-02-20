"""Tests for generic energy load CSV parsing."""

from pathlib import Path

from config.case_config import EnergyLoadFileConfig
from data_loading.loaders.energy_load import load_energy_demand_csv


def test_load_energy_demand_csv_smoke(tmp_path: Path):
    csv_path = tmp_path / "loads.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path)
    data = load_energy_demand_csv(cfg)

    assert len(data.indices["time"]) == 2
    assert data.timeseries["electricity_demand"] == [10.0, 11.5]
    assert data.static["time_step_hours"] == 1.0


def test_load_energy_demand_csv_fallback_detects_kw_column(tmp_path: Path):
    csv_path = tmp_path / "loads_kw.csv"
    csv_path.write_text(
        "Date,Campus Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(
        csv_path=csv_path,
        load_column="Electric Demand (kW)",
    )
    data = load_energy_demand_csv(cfg)

    assert data.timeseries["electricity_demand"] == [10.0, 11.5]
    assert data.static["load_units"] == "kW"


def test_load_energy_demand_csv_fallback_detects_kwh_column(tmp_path: Path):
    csv_path = tmp_path / "loads_kwh.csv"
    csv_path.write_text(
        "Date,Energy Use (kWh)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(
        csv_path=csv_path,
        load_column="Electric Demand (kW)",
    )
    data = load_energy_demand_csv(cfg)

    assert data.timeseries["electricity_demand"] == [10.0, 11.5]
    assert data.static["load_units"] == "kWh"


def test_load_energy_demand_csv_loads_multiple_columns(tmp_path: Path):
    csv_path = tmp_path / "loads_ambiguous.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW),Thermal Demand (kW)\n"
        "1/1/2022 0:00,10.0,8.0\n"
        "1/1/2022 1:00,11.5,8.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(
        csv_path=csv_path,
        load_column="Missing Column Name",
    )
    data = load_energy_demand_csv(cfg)

    assert data.timeseries["electricity_demand__electric_demand_kw"] == [10.0, 11.5]
    assert data.timeseries["electricity_demand__thermal_demand_kw"] == [8.0, 8.5]
    assert data.static["primary_load_column"] == "Electric Demand (kW)"


def test_load_energy_demand_csv_duplicate_headers(tmp_path: Path):
    csv_path = tmp_path / "loads_duplicate_headers.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW),Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0,8.0\n"
        "1/1/2022 1:00,11.5,8.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path)
    data = load_energy_demand_csv(cfg)

    assert data.timeseries["electricity_demand__electric_demand_kw"] == [10.0, 11.5]
    assert data.timeseries["electricity_demand__electric_demand_kw_2"] == [8.0, 8.5]

