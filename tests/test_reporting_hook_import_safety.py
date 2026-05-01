"""_load_reporting_hooks: only skip when .reporting is absent; surface real import errors."""

from __future__ import annotations

import pytest

from utilities.reporting.overarching_template import _load_reporting_hooks


def test_load_reporting_hooks_skips_when_reporting_submodule_missing() -> None:
    hooks = _load_reporting_hooks("tests.reporting_hook_fixtures.tech_no_reporting")
    assert hooks == {}


def test_load_reporting_hooks_loads_collect_block_functions() -> None:
    hooks = _load_reporting_hooks("tests.reporting_hook_fixtures.tech_with_hooks")
    assert set(hooks) == {
        "collect_block_report",
        "collect_block_timeseries",
        "collect_block_emissions",
    }
    assert hooks["collect_block_report"](None, None, None, {}) == {"fixture_report": 1}
    assert hooks["collect_block_timeseries"](None, None, None, {}) == {
        "fixture_ts": [1.0, 2.0],
    }
    assert hooks["collect_block_emissions"](None, None, None, {}) == {
        "aggregate": {"co2e_kg": 0.0},
    }


def test_load_reporting_hooks_propagates_import_error_inside_reporting_module() -> None:
    with pytest.raises(ImportError, match="broken_internal_import"):
        _load_reporting_hooks("tests.reporting_hook_fixtures.tech_broken_reporting")
