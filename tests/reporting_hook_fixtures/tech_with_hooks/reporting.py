"""Reporting hooks for hook-loader tests."""

from __future__ import annotations

from typing import Any


def collect_block_report(model, block, data, ctx) -> dict[str, Any]:
    return {"fixture_report": 1}


def collect_block_timeseries(model, block, data, ctx) -> dict[str, list[float]]:
    return {"fixture_ts": [1.0, 2.0]}


def collect_block_emissions(model, block, data, ctx) -> dict[str, Any]:
    return {"aggregate": {"co2e_kg": 0.0}}
