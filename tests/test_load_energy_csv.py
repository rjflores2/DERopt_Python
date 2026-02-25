"""Tests for generic energy load CSV and Excel parsing."""

from pathlib import Path

import pandas as pd

import pytest

from config.case_config import EnergyLoadFileConfig, discover_load_file, get_case_config
from data_loading.loaders.energy_load import load_energy_load


def test_load_energy_load_smoke(tmp_path: Path):
    csv_path = tmp_path / "loads.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,11.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path)
    data = load_energy_load(cfg)

    assert len(data.indices["time"]) == 2
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]
    assert data.static["time_step_hours"] == 1.0


def test_load_energy_load_fallback_detects_kw_column(tmp_path: Path):
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
    data = load_energy_load(cfg)

    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]  # hourly: kW×1 = kWh
    assert data.static["load_units"] == "kWh"  # converted from kW


def test_load_energy_load_fallback_detects_kwh_column(tmp_path: Path):
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
    data = load_energy_load(cfg)

    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]
    assert data.static["load_units"] == "kWh"


def test_load_energy_load_multiple_columns(tmp_path: Path):
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
    data = load_energy_load(cfg)

    assert data.static["load_columns"] == ["Electric Demand (kW)", "Thermal Demand (kW)"]
    assert data.timeseries["electricity_load__electric_load_kw"] == [10.0, 11.5]
    assert data.timeseries["electricity_load__thermal_load_kw"] == [8.0, 8.5]


def test_load_energy_load_duplicate_headers(tmp_path: Path):
    csv_path = tmp_path / "loads_duplicate_headers.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW),Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0,8.0\n"
        "1/1/2022 1:00,11.5,8.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path)
    data = load_energy_load(cfg)

    assert data.timeseries["electricity_load__electric_load_kw"] == [10.0, 11.5]
    assert data.timeseries["electricity_load__electric_load_kw_2"] == [8.0, 8.5]


def test_load_energy_load_three_columns_same_header_kw(tmp_path: Path):
    """Three load columns all named 'Electric Demand (kW)' (e.g. multi-node): deduped and all loaded with kW→kWh."""
    csv_path = tmp_path / "loads_three.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW),Electric Demand (kW),Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0,20.0,30.0\n"
        "1/1/2022 1:00,11.5,21.5,31.5\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path)
    data = load_energy_load(cfg)

    assert len(data.static["load_columns"]) == 3
    assert len(data.static["electricity_load_keys"]) == 3
    assert data.static["load_units"] == "kWh"
    assert data.timeseries["electricity_load__electric_load_kw"] == [10.0, 11.5]
    assert data.timeseries["electricity_load__electric_load_kw_2"] == [20.0, 21.5]
    assert data.timeseries["electricity_load__electric_load_kw_3"] == [30.0, 31.5]


def test_load_energy_load_excel_serial(tmp_path: Path):
    """Excel serial date column (e.g. 44562 = 2022-01-01)."""
    csv_path = tmp_path / "loads_excel_serial.csv"
    # Excel serial 44562 = 2022-01-01, 44563 = 2022-01-02
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "44562,10.0\n"
        "44563,11.5\n",
        encoding="utf-8",
    )
    cfg = EnergyLoadFileConfig(csv_path=csv_path, datetime_format="excel_serial")
    data = load_energy_load(cfg)
    assert len(data.indices["time"]) == 2
    # Daily data: 10 kW × 24 h = 240 kWh, 11.5 kW × 24 h = 276 kWh
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [240.0, 276.0]
    assert data.timeseries["datetime"][0].year == 2022
    assert data.timeseries["datetime"][0].month == 1
    assert data.timeseries["datetime"][0].day == 1


def test_load_energy_load_matlab_serial(tmp_path: Path):
    """MATLAB serial date column (e.g. 738885 for 2022)."""
    csv_path = tmp_path / "loads_matlab_serial.csv"
    # MATLAB serial for 2022-01-01 00:00 is ~738885
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "738885,10.0\n"
        "738886,11.5\n",
        encoding="utf-8",
    )
    cfg = EnergyLoadFileConfig(csv_path=csv_path, datetime_format="matlab_serial")
    data = load_energy_load(cfg)
    assert len(data.indices["time"]) == 2
    # Daily data: 10 kW × 24 h = 240 kWh, 11.5 kW × 24 h = 276 kWh
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [240.0, 276.0]
    assert data.timeseries["datetime"][0].year == 2022


def test_load_energy_load_auto_detects_excel_serial(tmp_path: Path):
    """Auto infers Excel when serial values are in Excel range."""
    csv_path = tmp_path / "loads_auto.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "44562,10.0\n"
        "44563,11.5\n",
        encoding="utf-8",
    )
    cfg = EnergyLoadFileConfig(csv_path=csv_path, datetime_format="auto")
    data = load_energy_load(cfg)
    assert len(data.indices["time"]) == 2
    assert len(data.static["electricity_load_keys"]) >= 1
    assert data.timeseries["datetime"][0].year == 2022


def test_load_energy_load_xlsx_smoke(tmp_path: Path):
    """Load from xlsx file (openpyxl engine)."""
    xlsx_path = tmp_path / "loads.xlsx"
    df = pd.DataFrame(
        {
            "Date": ["1/1/2022 0:00", "1/1/2022 1:00"],
            "Electric Demand (kW)": [10.0, 11.5],
        }
    )
    df.to_excel(xlsx_path, index=False)

    cfg = EnergyLoadFileConfig(csv_path=xlsx_path)
    data = load_energy_load(cfg)

    assert len(data.indices["time"]) == 2
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]
    assert data.static["time_step_hours"] == 1.0
    assert data.timeseries["datetime"][0].year == 2022


def test_load_energy_load_xlsx_sheet_name(tmp_path: Path):
    """Load from a specific sheet in a multi-sheet xlsx."""
    xlsx_path = tmp_path / "loads_multi_sheet.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame(
            {"Date": ["1/1/2022 0:00"], "Electric Demand (kW)": [99.0]}
        ).to_excel(writer, sheet_name="Wrong", index=False)
        pd.DataFrame(
            {
                "Date": ["1/1/2022 0:00", "1/1/2022 1:00"],
                "Electric Demand (kW)": [10.0, 11.5],
            }
        ).to_excel(writer, sheet_name="LoadData", index=False)

    cfg = EnergyLoadFileConfig(csv_path=xlsx_path, sheet_name="LoadData")
    data = load_energy_load(cfg)

    assert len(data.indices["time"]) == 2
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]


def test_discover_load_file_finds_xlsx(tmp_path: Path):
    """discover_load_file prefers xlsx when both csv and xlsx exist."""
    (tmp_path / "other.csv").write_text("x")
    xlsx_path = tmp_path / "Electric_Loads.xlsx"
    pd.DataFrame(
        {"Date": ["1/1/2022 0:00"], "Electric Demand (kW)": [10.0]}
    ).to_excel(xlsx_path, index=False)
    (tmp_path / "loads.csv").write_text("Date,Electric Demand (kW)\n1/1/2022,10")

    found = discover_load_file(tmp_path)
    assert found == xlsx_path


def test_discover_load_file_raises_when_empty(tmp_path: Path):
    """discover_load_file raises when no load files exist."""
    with pytest.raises(FileNotFoundError, match="No load files"):
        discover_load_file(tmp_path)


def test_load_from_discovered_folder(tmp_path: Path):
    """Load energy data using discover_load_file to find the file."""
    xlsx_path = tmp_path / "Igiugig_Electric_Loads.xlsx"
    pd.DataFrame(
        {
            "Date": ["1/1/2022 0:00", "1/1/2022 1:00"],
            "Electric Demand (kW)": [10.0, 11.5],
        }
    ).to_excel(xlsx_path, index=False)

    load_path = discover_load_file(tmp_path)
    cfg = EnergyLoadFileConfig(csv_path=load_path)
    data = load_energy_load(cfg)

    assert len(data.indices["time"]) == 2
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 11.5]


def test_get_case_config_returns_expected_paths():
    """All case configs return expected csv_path structure."""
    project_root = Path("/fake/project")
    for case_name, expected_parts in [
        ("igiugig", ("Igiugig", "Igiugig_Electric_Loads.csv")),
        ("igiugig multi node", ("Igiugig_Multi_Node", "Igiugig_Electric_Loads.csv")),
    ]:
        cfg = get_case_config(project_root, case_name)
        path_parts = cfg.energy_load.csv_path.parts
        assert path_parts[-2:] == expected_parts


def test_time_conditioning_resamples_to_regular_grid(tmp_path: Path):
    """Time conditioning regularizes irregular timestamps to hourly grid."""
    csv_path = tmp_path / "loads_irregular.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:30,12.0\n"
        "1/1/2022 3:00,14.0\n",
        encoding="utf-8",
    )
    cfg = EnergyLoadFileConfig(
        csv_path=csv_path,
        target_interval_minutes=60,
        interpolation_method="linear",
    )
    data = load_energy_load(cfg)
    # Resampled to hourly: 0:00, 1:00, 2:00, 3:00
    assert len(data.indices["time"]) == 4
    assert data.static["time_step_hours"] == 1.0
    loads = data.timeseries[data.static["electricity_load_keys"][0]]
    assert loads[0] == 10.0
    assert loads[3] == 14.0


def test_missing_load_values_interpolate_without_resample(tmp_path: Path):
    """Missing values interpolate even when target_interval_minutes is not configured."""
    csv_path = tmp_path / "loads_missing_no_resample.csv"
    csv_path.write_text(
        "Date,Electric Demand (kW)\n"
        "1/1/2022 0:00,10.0\n"
        "1/1/2022 1:00,\n"
        "1/1/2022 2:00,14.0\n",
        encoding="utf-8",
    )

    cfg = EnergyLoadFileConfig(csv_path=csv_path, target_interval_minutes=None)
    data = load_energy_load(cfg)

    assert len(data.indices["time"]) == 3
    key0 = data.static["electricity_load_keys"][0]
    assert data.timeseries[key0] == [10.0, 12.0, 14.0]


def test_igiugig_xlsx_case_loads():
    """Igiugig xlsx case loads when data folder exists."""
    project_root = Path(__file__).resolve().parents[1]
    folder = project_root / "data" / "Igiugig_xlsx"
    if not folder.is_dir():
        pytest.skip("data/Igiugig_xlsx folder not present (gitignored)")

    cfg = get_case_config(project_root, "igiugig xlsx")
    data = load_energy_load(cfg.energy_load)
    assert len(data.indices["time"]) > 0
    assert len(data.static["electricity_load_keys"]) > 0

