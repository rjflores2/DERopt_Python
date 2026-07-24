"""Two-stage stochastic scenario config: validation and data-loading behavior.

Mirrors the pytest.raises(ValueError, match=...) style used in test_utility_mode_config.py
and test_per_node_utility_tariffs.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import run.build_run_data as brd
from config.case_config import CaseConfig, EnergyLoadFileConfig, ScenarioConfig, UtilityTariffConfig
from data_loading.schemas import DataContainer


def _one_node_data() -> DataContainer:
    return DataContainer(
        indices={"time": [0, 1]},
        timeseries={"time_serial": [0, 1], "electricity_load__x": [1.0, 2.0]},
        static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
    )


def _base_case_cfg(tmp_path: Path, **overrides) -> SimpleNamespace:
    case_cfg = SimpleNamespace(
        energy_load=SimpleNamespace(csv_path=tmp_path / "loads.csv"),
        solar_path=None,
        wind_path=None,
        utility_rate_path=None,
        energy_price_path=None,
        utility_tariffs=None,
        time_subset=None,
        utility_mode="free_grid",
        **overrides,
    )
    case_cfg.energy_load.csv_path.write_text("Date,Electric Demand (kW)\n", encoding="utf-8")
    return case_cfg


def test_no_scenarios_is_the_default_implicit_single_scenario(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(tmp_path)  # no `scenarios` attribute at all, like today's cases

    data = brd.build_run_data(tmp_path, case_cfg)

    assert data.scenario_keys == ["_default"]
    assert data.scenario_probability == {"_default": 1.0}
    assert data.scenario_timeseries == {}


def test_single_scenario_probability_none_is_forced_to_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path, scenarios=[ScenarioConfig(scenario_key="only_one")]
    )

    data = brd.build_run_data(tmp_path, case_cfg)

    assert data.scenario_keys == ["only_one"]
    assert data.scenario_probability == {"only_one": 1.0}


def test_single_scenario_probability_not_settable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path, scenarios=[ScenarioConfig(scenario_key="only_one", probability=0.5)]
    )
    with pytest.raises(ValueError, match="not user-settable"):
        brd.build_run_data(tmp_path, case_cfg)


def test_multi_scenario_missing_probability_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=0.5),
            ScenarioConfig(scenario_key="dry"),
        ],
    )
    with pytest.raises(ValueError, match="must set probability"):
        brd.build_run_data(tmp_path, case_cfg)


def test_multi_scenario_non_positive_probability_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=1.2),
            ScenarioConfig(scenario_key="dry", probability=-0.2),
        ],
    )
    with pytest.raises(ValueError, match="must be > 0"):
        brd.build_run_data(tmp_path, case_cfg)


def test_multi_scenario_probabilities_must_sum_to_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=0.3),
            ScenarioConfig(scenario_key="dry", probability=0.3),
        ],
    )
    with pytest.raises(ValueError, match="must sum to 1.0"):
        brd.build_run_data(tmp_path, case_cfg)


def test_duplicate_scenario_key_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=0.5),
            ScenarioConfig(scenario_key="wet", probability=0.5),
        ],
    )
    with pytest.raises(ValueError, match="duplicate scenario keys"):
        brd.build_run_data(tmp_path, case_cfg)


def test_empty_scenario_key_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path, scenarios=[ScenarioConfig(scenario_key="  ")]
    )
    with pytest.raises(ValueError, match="non-empty"):
        brd.build_run_data(tmp_path, case_cfg)


def test_empty_scenarios_list_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(tmp_path, scenarios=[])
    with pytest.raises(ValueError, match="must be non-empty"):
        brd.build_run_data(tmp_path, case_cfg)


def test_partial_override_coverage_raises(tmp_path: Path, monkeypatch) -> None:
    """One scenario sets solar_path, its sibling doesn't -> reject, don't silently fall back."""
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=0.5, solar_path=tmp_path / "wet_solar.csv"),
            ScenarioConfig(scenario_key="dry", probability=0.5),
        ],
    )
    with pytest.raises(ValueError, match="'solar_path' must be set on all scenarios or none"):
        brd.build_run_data(tmp_path, case_cfg)


def test_node_utility_tariff_without_utility_tariffs_on_same_scenario_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """node_utility_tariff selects among a scenario's OWN utility_tariffs; setting it alone
    would otherwise be silently ignored (found in review) -- must fail fast instead."""
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(
                scenario_key="only_one",
                node_utility_tariff={"electricity_load__x": "commercial"},
            ),
        ],
    )
    with pytest.raises(ValueError, match="'node_utility_tariff' requires 'utility_tariffs'"):
        brd.build_run_data(tmp_path, case_cfg)


def test_node_utility_tariff_need_not_be_set_on_every_scenario(tmp_path: Path, monkeypatch) -> None:
    """Unlike solar_path/hydrokinetic_path/etc., node_utility_tariff is an optional per-node
    modifier of utility_tariffs, not an independent override category -- one scenario may
    override the node->tariff assignment while its sibling (which also sets utility_tariffs)
    is content with the default assignment."""
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())

    class FakeRaw:
        def __init__(self, prices):
            self.prices = prices

    monkeypatch.setattr(brd, "load_raw_energy_prices", lambda path, **_: FakeRaw([0.1, 0.2]))
    monkeypatch.setattr(
        brd, "get_import_prices_for_timestamps", lambda source, timestamps: list(source.prices)
    )

    price_path = tmp_path / "p.csv"
    price_path.write_text("price\n0.1\n0.2\n", encoding="utf-8")

    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(
                scenario_key="wet",
                probability=0.5,
                utility_tariffs=[UtilityTariffConfig(tariff_key="default", energy_price_path=price_path)],
                node_utility_tariff={"electricity_load__x": "default"},
            ),
            ScenarioConfig(
                scenario_key="dry",
                probability=0.5,
                utility_tariffs=[UtilityTariffConfig(tariff_key="default", energy_price_path=price_path)],
            ),
        ],
    )

    # Should not raise -- node_utility_tariff completeness is not enforced all-or-nothing.
    data = brd.build_run_data(tmp_path, case_cfg)
    assert data.scenario_keys == ["wet", "dry"]


def test_scenario_utility_tariff_without_price_source_raises(tmp_path: Path, monkeypatch) -> None:
    """The base case rejects a utility_tariffs entry with no utility_rate_path/energy_price_path
    (via _validate_utility_mode_config); a scenario-level tariff entry must be rejected the
    same way instead of silently resolving to a $0/kWh import price (found in review)."""
    monkeypatch.setattr(brd, "load_energy_load", lambda _cfg: _one_node_data())
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(
                scenario_key="only_one",
                utility_tariffs=[UtilityTariffConfig(tariff_key="default")],
            ),
        ],
    )
    with pytest.raises(ValueError, match="must set utility_rate_path and/or energy_price_path"):
        brd.build_run_data(tmp_path, case_cfg)


def test_multi_scenario_energy_load_override_populates_scenario_timeseries(
    tmp_path: Path, monkeypatch
) -> None:
    base = _one_node_data()

    def fake_load_energy_load(cfg):
        # Base call passes case_cfg.energy_load; scenario calls pass each ScenarioConfig's own.
        if cfg is case_cfg.energy_load:
            return base
        if getattr(cfg, "csv_path", None) == tmp_path / "wet.csv":
            return DataContainer(
                indices={"time": [0, 1]},
                timeseries={"time_serial": [0, 1], "electricity_load__x": [3.0, 4.0]},
                static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
            )
        if getattr(cfg, "csv_path", None) == tmp_path / "dry.csv":
            return DataContainer(
                indices={"time": [0, 1]},
                timeseries={"time_serial": [0, 1], "electricity_load__x": [5.0, 6.0]},
                static={"electricity_load_keys": ["electricity_load__x"], "time_step_hours": 1.0},
            )
        raise AssertionError(f"unexpected energy_load cfg: {cfg!r}")

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)

    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[
            ScenarioConfig(
                scenario_key="wet",
                probability=0.4,
                energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "wet.csv"),
            ),
            ScenarioConfig(
                scenario_key="dry",
                probability=0.6,
                energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "dry.csv"),
            ),
        ],
    )

    data = brd.build_run_data(tmp_path, case_cfg)

    assert data.scenario_keys == ["wet", "dry"]
    assert data.scenario_probability == {"wet": 0.4, "dry": 0.6}
    assert data.scenario_timeseries["wet"]["electricity_load__x"] == [3.0, 4.0]
    assert data.scenario_timeseries["dry"]["electricity_load__x"] == [5.0, 6.0]
    # Base data.timeseries is untouched -- scenarios only add sparse overrides.
    assert data.timeseries["electricity_load__x"] == [1.0, 2.0]


def test_energy_load_override_node_mismatch_raises(tmp_path: Path, monkeypatch) -> None:
    def fake_load_energy_load(cfg):
        if cfg is case_cfg.energy_load:
            return _one_node_data()
        return DataContainer(
            indices={"time": [0, 1]},
            timeseries={"time_serial": [0, 1], "electricity_load__other": [1.0, 1.0]},
            static={"electricity_load_keys": ["electricity_load__other"], "time_step_hours": 1.0},
        )

    monkeypatch.setattr(brd, "load_energy_load", fake_load_energy_load)
    case_cfg = _base_case_cfg(
        tmp_path,
        scenarios=[ScenarioConfig(scenario_key="only_one", energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "x.csv"))],
    )
    with pytest.raises(ValueError, match="must define the same nodes"):
        brd.build_run_data(tmp_path, case_cfg)


def test_scenarios_survive_case_config_dataclass_construction(tmp_path: Path) -> None:
    """CaseConfig itself (not just SimpleNamespace fixtures) accepts scenarios."""
    cfg = CaseConfig(
        case_name="scenario_smoke_test",
        energy_load=EnergyLoadFileConfig(csv_path=tmp_path / "loads.csv"),
        scenarios=[
            ScenarioConfig(scenario_key="wet", probability=0.5),
            ScenarioConfig(scenario_key="dry", probability=0.5),
        ],
    )
    assert cfg.scenarios is not None
    assert [s.scenario_key for s in cfg.scenarios] == ["wet", "dry"]
