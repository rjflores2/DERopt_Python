"""Tests for resource profile loaders (solar, etc.)."""

from pathlib import Path

import pytest

from config.case_config import (
    EnergyLoadFileConfig,
    discover_hydrokinetic_file,
    discover_solar_file,
)
from data_loading.loaders.energy_load import load_energy_load
from data_loading.loaders.resource_profiles import (
    load_hydrokinetic_into_container,
    load_solar_into_container,
)
from data_loading.schemas import DataContainer


def test_solar_no_time_column_8760(tmp_path: Path):
    """Solar file with no time column: 8760 rows = hourly, one year."""
    # Minimal load file to get a time vector (2 hours)
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.5\n",
        encoding="utf-8",
    )
    # Solar: 8760 rows, one column (no header time column)
    solar_path = tmp_path / "solar.csv"
    header = "solar_cf\n"
    rows = "\n".join(["0.5"] * 8760)  # constant 0.5 for simplicity
    solar_path.write_text(header + rows, encoding="utf-8")

    cfg = EnergyLoadFileConfig(csv_path=load_path)
    data = load_energy_load(cfg)
    load_solar_into_container(data, solar_path)

    assert "solar_production_keys" in data.static
    key = data.static["solar_production_keys"][0]
    assert key == "solar_production__solar_cf"
    assert len(data.timeseries[key]) == 2
    # CF 0.5 × dt_hours 1.0 = 0.5 kWh/kW
    assert data.timeseries[key][0] == 0.5
    assert data.timeseries[key][1] == 0.5
    assert data.static["solar_production_units"] == "kWh/kW"


def test_solar_with_time_column(tmp_path: Path):
    """Solar file with time column; align to load by time-of-year."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,0.2\n"
        "1/1/2022 2:00,0.3\n",
        encoding="utf-8",
    )
    # Solar has 3 rows with time (same time-of-day as load)
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text(
        "Date,Capacity Factor\n"
        "1/1/2020 0:00,0.10\n"
        "1/1/2020 1:00,0.20\n"
        "1/1/2020 2:00,0.30\n",
        encoding="utf-8",
    )

    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_solar_into_container(data, solar_path)

    key = data.static["solar_production_keys"][0]
    assert len(data.timeseries[key]) == 3
    # CF × dt_hours(1.0) = kWh/kW
    assert data.timeseries[key][0] == pytest.approx(0.10)
    assert data.timeseries[key][1] == pytest.approx(0.20)
    assert data.timeseries[key][2] == pytest.approx(0.30)


def test_solar_multiple_columns(tmp_path: Path):
    """Solar file with multiple columns (e.g. fixed vs tracking)."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n",
        encoding="utf-8",
    )
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text(
        "Date,Fixed (kW/kW),1D Tracking (kW/kW)\n"
        "1/1/2020 0:00,0.0,0.0\n"
        "1/1/2020 1:00,0.1,0.15\n",
        encoding="utf-8",
    )

    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_solar_into_container(data, solar_path)

    assert "solar_production__fixed_kw_kw" in data.timeseries
    assert "solar_production__1d_tracking_kw_kw" in data.timeseries
    assert data.timeseries["solar_production__fixed_kw_kw"] == [0.0, 0.1]
    assert data.timeseries["solar_production__1d_tracking_kw_kw"] == [0.0, 0.15]


def test_solar_missing_file_raises(tmp_path: Path):
    """load_solar_into_container raises if file does not exist."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text("Date,Electric Demand (kW)\n1/1/2022 0:00,10.0\n", encoding="utf-8")
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))

    with pytest.raises(FileNotFoundError, match="Solar file not found"):
        load_solar_into_container(data, tmp_path / "nonexistent_solar.csv")


def test_discover_solar_file_finds_csv(tmp_path: Path):
    """discover_solar_file returns csv/xlsx with 'solar' in name (case-insensitive)."""
    (tmp_path / "other.csv").write_text("x")
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text("solar_cf\n0.5\n")
    found = discover_solar_file(tmp_path)
    assert found == solar_path


def test_discover_solar_file_prefers_xlsx(tmp_path: Path):
    """discover_solar_file prefers xlsx when both solar.csv and solar.xlsx exist."""
    (tmp_path / "solar.csv").write_text("solar_cf\n0.5\n")
    xlsx_path = tmp_path / "Solar_Profile.xlsx"
    xlsx_path.write_bytes(b"dummy")  # minimal placeholder
    found = discover_solar_file(tmp_path)
    assert found == xlsx_path


def test_discover_solar_file_returns_none_when_empty(tmp_path: Path):
    """discover_solar_file returns None when no solar file in folder."""
    (tmp_path / "loads.csv").write_text("Date,Electric Demand (kW)\n1/1/2022,10\n")
    assert discover_solar_file(tmp_path) is None


def test_hydrokinetic_multiple_location_columns(tmp_path: Path):
    """HKT file: kW power per column, aligned to load datetimes (calendar anchor)."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n",
        encoding="utf-8",
    )
    hkt_path = tmp_path / "hkt_potential.csv"
    hkt_path.write_text(
        "Date,Upstream kW/kW,Mid kW/kW\n"
        "1/1/2020 0:00,0.2,0.25\n"
        "1/1/2020 1:00,0.3,0.35\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_hydrokinetic_into_container(data, hkt_path)

    assert data.static["hydrokinetic_production_keys"] == [
        "hydrokinetic_production__upstream_kw_kw",
        "hydrokinetic_production__mid_kw_kw",
    ]
    assert data.timeseries["hydrokinetic_production__upstream_kw_kw"] == [0.2, 0.3]
    assert data.timeseries["hydrokinetic_production__mid_kw_kw"] == [0.25, 0.35]
    assert data.static["hydrokinetic_production_units"] == "kWh/kW"


def test_discover_hydrokinetic_file_finds_hkt_potential_csv(tmp_path: Path):
    """discover_hydrokinetic_file matches stem containing 'hkt' or 'hydrokinetic'."""
    (tmp_path / "loads.csv").write_text("x")
    hkt = tmp_path / "hkt_potential.csv"
    hkt.write_text("Date,a\n1/1/2020,1\n")
    assert discover_hydrokinetic_file(tmp_path) == hkt


def test_discover_hydrokinetic_file_prefers_xlsx(tmp_path: Path):
    """discover_hydrokinetic_file prefers xlsx over csv when both match."""
    (tmp_path / "hkt_potential.csv").write_text("a\n1\n")
    xlsx = tmp_path / "Hydrokinetic_profile.xlsx"
    xlsx.write_bytes(b"dummy")
    assert discover_hydrokinetic_file(tmp_path) == xlsx


def test_discover_hydrokinetic_file_returns_none_when_empty(tmp_path: Path):
    assert discover_hydrokinetic_file(tmp_path) is None


def test_hydrokinetic_coerces_object_column(tmp_path: Path):
    """HKT value columns read as object still coerce (blanks / text → NaN, then interpolate)."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n",
        encoding="utf-8",
    )
    hkt_path = tmp_path / "hkt.csv"
    hkt_path.write_text(
        "Date,Site\n"
        "1/1/2020 0:00,0.2\n"
        "1/1/2020 1:00,n/a\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_hydrokinetic_into_container(data, hkt_path)
    key = data.static["hydrokinetic_production_keys"][0]
    assert data.timeseries[key][0] == pytest.approx(0.2)
    assert not (data.timeseries[key][1] != data.timeseries[key][1])


def test_hydrokinetic_irregular_timestamps_interpolates(tmp_path: Path):
    """Non-hourly / sparse HKT rows: time-interpolate to each load timestep."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n"
        "1/1/2022 2:00,12.0\n",
        encoding="utf-8",
    )
    hkt_path = tmp_path / "hkt.csv"
    hkt_path.write_text(
        "Date,SiteA (kW)\n"
        "1/1/2020 0:00,0.0\n"
        "1/1/2020 2:00,2.0\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_hydrokinetic_into_container(data, hkt_path)
    key = data.static["hydrokinetic_production_keys"][0]
    # Anchored same month/day/hour: 1:00 linear between 0 and 2 kW → 1.0 kW × 1 h = 1.0 kWh/kW
    assert data.timeseries[key][0] == pytest.approx(0.0)
    assert data.timeseries[key][1] == pytest.approx(1.0)
    assert data.timeseries[key][2] == pytest.approx(2.0)


def test_hydrokinetic_reference_kw_absolute_power(tmp_path: Path):
    """reference_kw scales absolute kW in file to kWh per kW installed."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n",
        encoding="utf-8",
    )
    hkt_path = tmp_path / "hkt.csv"
    hkt_path.write_text(
        "Date,Potential (kW)\n"
        "1/1/2020 0:00,50.0\n"
        "1/1/2020 1:00,50.0\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_hydrokinetic_into_container(data, hkt_path, reference_kw=100.0)
    key = data.static["hydrokinetic_production_keys"][0]
    assert data.static["hydrokinetic_reference_kw"] == 100.0
    # (50/100) kW per kW × 1 h = 0.5 kWh/kW
    assert data.timeseries[key][0] == pytest.approx(0.5)
    assert data.timeseries[key][1] == pytest.approx(0.5)


def test_solar_coerces_object_column_mixed_text(tmp_path: Path):
    """Object dtype columns (blanks, stray text) coerce to numeric; gaps interpolate."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n"
        "1/1/2022 2:00,12.0\n",
        encoding="utf-8",
    )
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text(
        "Date,CF\n"
        "1/1/2020 0:00,0.1\n"
        "1/1/2020 1:00,bad\n"
        "1/1/2020 2:00,0.3\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_solar_into_container(data, solar_path)
    key = data.static["solar_production_keys"][0]
    assert all(not (v != v) for v in data.timeseries[key])
    assert data.timeseries[key][0] == pytest.approx(0.1)
    assert data.timeseries[key][2] == pytest.approx(0.3)


def test_solar_all_negative_raises_clear_error(tmp_path: Path):
    """All-negative resource becomes all-NaN after masking; loader raises instead of Pyomo."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n",
        encoding="utf-8",
    )
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text(
        "Date,CF\n"
        "1/1/2020 0:00,-0.5\n"
        "1/1/2020 1:00,-0.2\n",
        encoding="utf-8",
    )
    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    with pytest.raises(ValueError, match="NaN remains in columns"):
        load_solar_into_container(data, solar_path)


def test_solar_filtering_negative_and_nan(tmp_path: Path):
    """Solar loader replaces negatives and fills NaNs so output has no negative/NaN."""
    load_path = tmp_path / "loads.csv"
    load_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.0\n"
        "1/1/2022 2:00,12.0\n"
        "1/1/2022 3:00,13.0\n",
        encoding="utf-8",
    )
    # Solar: one negative, one NaN; should be filled by interpolation
    solar_path = tmp_path / "solar.csv"
    solar_path.write_text(
        "Date,CF\n"
        "1/1/2020 0:00,0.1\n"
        "1/1/2020 1:00,-0.5\n"
        "1/1/2020 2:00,\n"
        "1/1/2020 3:00,0.4\n",
        encoding="utf-8",
    )

    data = load_energy_load(EnergyLoadFileConfig(csv_path=load_path))
    load_solar_into_container(data, solar_path)

    key = data.static["solar_production_keys"][0]
    prod = data.timeseries[key]
    assert len(prod) == 4
    assert all(isinstance(v, (int, float)) and not (v != v) for v in prod)  # no NaN
    assert all(v >= 0 for v in prod)  # no negative
