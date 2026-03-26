"""TOU OpenEI tariffs require load datetimes for schedule mapping."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import run.build_run_data as brd
from data_loading.schemas import DataContainer
from data_loading.loaders.utility_rates.openei_router import ParsedRate


def test_build_run_data_tou_rate_without_datetime_raises(monkeypatch, tmp_path: Path):
    # Fake load data container without timeseries["datetime"] (bypass load_energy_load).
    def fake_load_energy_load(_cfg):
        return DataContainer(
            indices={"time": [0]},
            timeseries={"time_serial": [0], "electricity_load__x": [1.0]},
            static={"electricity_load_keys": ["electricity_load__x"]},
        )

    def fake_load_openei_rate(_path, *, item_index=None):
        return ParsedRate(rate_type="tou", utility="X", name="Y", payload={"import_prices_12x24_weekday": [], "import_prices_12x24_weekend": []})

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    monkeypatch.setattr(brd, "load_openei_rate", fake_load_openei_rate)

    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        energy_price_path=None,
        energy_price_column=None,
        utility_rate_path=tmp_path / "rate.json",
        utility_rate_item_index=None,
        time_subset=None,
    )

    # build_run_data checks that the paths exist, so create placeholders.
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    case_cfg.utility_rate_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="TOU tariff.*no timeseries\\['datetime'\\]"):
        brd.build_run_data(tmp_path, case_cfg)

