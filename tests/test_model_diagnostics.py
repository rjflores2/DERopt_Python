"""Tests for pre-solve model diagnostics (warnings only)."""

from types import SimpleNamespace

from utilities.model_diagnostics import collect_model_diagnostics, print_model_diagnostics


def test_time_subset_triggers_horizon_warning():
    data = SimpleNamespace(
        indices={"time": list(range(24))},
        static={"time_subset_applied": {"reason": "test"}, "time_step_hours": 1.0},
        import_prices=None,
        utility_rate=None,
    )
    model = SimpleNamespace()
    w = collect_model_diagnostics(model, data, None)
    assert any("time_subset_applied" in x for x in w)


def test_short_horizon_warning():
    data = SimpleNamespace(
        indices={"time": list(range(100))},
        static={"time_step_hours": 1.0},
        import_prices=None,
        utility_rate=None,
    )
    w = collect_model_diagnostics(SimpleNamespace(), data, None)
    assert any("full year" in x.lower() for x in w)


def test_negative_import_prices_warning_includes_min():
    data = SimpleNamespace(
        indices={"time": [0]},
        static={},
        import_prices=[0.1, -0.02],
        utility_rate=None,
    )
    w = collect_model_diagnostics(SimpleNamespace(), data, None)
    assert any("negative" in x.lower() and "-0.02" in x for x in w)


def test_print_model_diagnostics_is_silent_when_empty(capsys):
    print_model_diagnostics([])
    assert capsys.readouterr().out == ""


def test_print_model_diagnostics_format(capsys):
    print_model_diagnostics(["one warning"])
    out = capsys.readouterr().out
    assert "Model diagnostics:" in out
    assert "- one warning" in out


def test_collect_returns_list():
    data = SimpleNamespace(
        indices={"time": [0]},
        static={},
        import_prices=[0.1],
        utility_rate=None,
    )
    w = collect_model_diagnostics(SimpleNamespace(), data, None)
    assert isinstance(w, list)
