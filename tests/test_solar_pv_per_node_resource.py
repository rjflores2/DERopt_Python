"""Per-node solar resource override (Option A for distributed-site solar).

Verifies:
1. Default behavior — no override — broadcasts the canonical per-profile time series to
   every node (co-located / single-site case; preserves pre-refactor behavior).
2. Per-node override — a ``solar_resource_assignment_by_node_and_profile`` entry for a
   specific ``(node, profile)`` pair makes that cell draw from a different
   ``data.timeseries`` key; other (node, profile) cells continue to use the default.
3. Input validation — unknown node, unknown profile, and missing ``data.timeseries``
   keys raise clear errors.

The tests build a solar_pv block directly on a minimal model (no solver invocation
needed — they check the Pyomo Param values).
"""

from __future__ import annotations

import pyomo.environ as pyo
import pytest

from data_loading.schemas import DataContainer
from technologies.solar_pv.block import add_solar_pv_block


def _build_minimal_model(nodes: list[str], horizon_len: int) -> pyo.ConcreteModel:
    """Two-node, short-horizon shell sufficient for the solar block to attach."""
    m = pyo.ConcreteModel()
    m.T = pyo.Set(initialize=list(range(horizon_len)), ordered=True)
    m.NODES = pyo.Set(initialize=nodes, ordered=True)
    return m


def _build_data_container(
    *,
    horizon_len: int,
    solar_series_by_key: dict[str, list[float]],
    solar_production_keys: list[str],
) -> DataContainer:
    """DataContainer with just enough to satisfy the solar block's reads."""
    data = DataContainer()
    data.timeseries = dict(solar_series_by_key)
    data.static = {
        "solar_production_keys": list(solar_production_keys),
        "time_step_hours": 1.0,
    }
    data.indices = {"time": list(range(horizon_len))}
    return data


# Canonical per-profile series — used as the default at every node unless overridden.
_DEFAULT_FIXED_TILT_SERIES = [0.10, 0.20, 0.30, 0.40]

# Alternate series for a different site (e.g. higher-latitude resource) — only used when
# explicitly assigned to a (node, profile) cell.
_SITE_B_FIXED_TILT_SERIES = [0.05, 0.08, 0.12, 0.16]


def test_default_broadcasts_profile_series_to_every_node() -> None:
    """No resource-assignment input -> each node sees the profile's canonical series.

    This is the pre-refactor behavior and must stay the default to avoid breaking the
    co-located-nodes cases that DERopt is tuned for today.
    """
    horizon_len = len(_DEFAULT_FIXED_TILT_SERIES)
    nodes = ["node_A", "node_B"]
    model = _build_minimal_model(nodes, horizon_len)
    data = _build_data_container(
        horizon_len=horizon_len,
        solar_series_by_key={"solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES},
        solar_production_keys=["solar_production__fixed_tilt"],
    )

    add_solar_pv_block(model, data, solar_pv_params={"allow_adoption": True})

    for node in nodes:
        for t_idx, expected in enumerate(_DEFAULT_FIXED_TILT_SERIES):
            actual = float(
                pyo.value(model.solar_pv.solar_potential[node, "solar_production__fixed_tilt", t_idx])
            )
            assert actual == pytest.approx(expected), (
                f"Default (broadcast) failed at (node={node!r}, t={t_idx}): "
                f"got {actual}, expected {expected}"
            )


def test_per_node_override_applies_only_to_assigned_cells() -> None:
    """With an assignment for (node_B, fixed_tilt), node_B uses the alternate series
    while node_A continues to use the canonical broadcast series.

    This is the core Option A capability: different latitudes / microclimates get
    different resource data without any cost-row duplication.
    """
    horizon_len = len(_DEFAULT_FIXED_TILT_SERIES)
    nodes = ["node_A", "node_B"]
    model = _build_minimal_model(nodes, horizon_len)
    data = _build_data_container(
        horizon_len=horizon_len,
        solar_series_by_key={
            "solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES,
            "solar_production__fixed_tilt__site_b": _SITE_B_FIXED_TILT_SERIES,
        },
        solar_production_keys=["solar_production__fixed_tilt"],
    )
    assignment = {
        "node_B": {"solar_production__fixed_tilt": "solar_production__fixed_tilt__site_b"}
    }

    add_solar_pv_block(
        model,
        data,
        solar_pv_params={
            "allow_adoption": True,
            "solar_resource_assignment_by_node_and_profile": assignment,
        },
    )

    for t_idx, expected in enumerate(_DEFAULT_FIXED_TILT_SERIES):
        actual = float(
            pyo.value(model.solar_pv.solar_potential["node_A", "solar_production__fixed_tilt", t_idx])
        )
        assert actual == pytest.approx(expected), (
            f"Unassigned node_A should keep broadcast series at t={t_idx}: "
            f"got {actual}, expected {expected}"
        )

    for t_idx, expected in enumerate(_SITE_B_FIXED_TILT_SERIES):
        actual = float(
            pyo.value(model.solar_pv.solar_potential["node_B", "solar_production__fixed_tilt", t_idx])
        )
        assert actual == pytest.approx(expected), (
            f"Assigned node_B should use site_b series at t={t_idx}: "
            f"got {actual}, expected {expected}"
        )


def test_tuple_keyed_assignment_accepted() -> None:
    """Mirror the existing *_by_node_and_profile convention: tuple-keyed dicts work too.

    Consistency with existing_solar_capacity_by_node_and_profile /
    max_capacity_area_by_node_and_profile keeps the API predictable.
    """
    horizon_len = len(_DEFAULT_FIXED_TILT_SERIES)
    nodes = ["node_A", "node_B"]
    model = _build_minimal_model(nodes, horizon_len)
    data = _build_data_container(
        horizon_len=horizon_len,
        solar_series_by_key={
            "solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES,
            "solar_production__fixed_tilt__site_b": _SITE_B_FIXED_TILT_SERIES,
        },
        solar_production_keys=["solar_production__fixed_tilt"],
    )

    add_solar_pv_block(
        model,
        data,
        solar_pv_params={
            "allow_adoption": True,
            "solar_resource_assignment_by_node_and_profile": {
                ("node_B", "solar_production__fixed_tilt"): "solar_production__fixed_tilt__site_b"
            },
        },
    )

    assert float(
        pyo.value(model.solar_pv.solar_potential["node_B", "solar_production__fixed_tilt", 0])
    ) == pytest.approx(_SITE_B_FIXED_TILT_SERIES[0])


def test_unknown_node_in_assignment_raises() -> None:
    """Fail fast on typos rather than silently producing wrong results."""
    model = _build_minimal_model(["node_A"], len(_DEFAULT_FIXED_TILT_SERIES))
    data = _build_data_container(
        horizon_len=len(_DEFAULT_FIXED_TILT_SERIES),
        solar_series_by_key={"solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES},
        solar_production_keys=["solar_production__fixed_tilt"],
    )
    with pytest.raises(ValueError, match="unknown node"):
        add_solar_pv_block(
            model,
            data,
            solar_pv_params={
                "solar_resource_assignment_by_node_and_profile": {
                    "node_typo": {
                        "solar_production__fixed_tilt": "solar_production__fixed_tilt"
                    }
                }
            },
        )


def test_unknown_profile_in_assignment_raises() -> None:
    model = _build_minimal_model(["node_A"], len(_DEFAULT_FIXED_TILT_SERIES))
    data = _build_data_container(
        horizon_len=len(_DEFAULT_FIXED_TILT_SERIES),
        solar_series_by_key={"solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES},
        solar_production_keys=["solar_production__fixed_tilt"],
    )
    with pytest.raises(ValueError, match="unknown profile"):
        add_solar_pv_block(
            model,
            data,
            solar_pv_params={
                "solar_resource_assignment_by_node_and_profile": {
                    "node_A": {"solar_tracking": "solar_production__fixed_tilt"}
                }
            },
        )


def test_missing_resource_key_in_timeseries_raises() -> None:
    """An assignment pointing at a nonexistent ``data.timeseries`` key must fail before
    Pyomo sees an unresolvable initializer.
    """
    model = _build_minimal_model(["node_A"], len(_DEFAULT_FIXED_TILT_SERIES))
    data = _build_data_container(
        horizon_len=len(_DEFAULT_FIXED_TILT_SERIES),
        solar_series_by_key={"solar_production__fixed_tilt": _DEFAULT_FIXED_TILT_SERIES},
        solar_production_keys=["solar_production__fixed_tilt"],
    )
    with pytest.raises(ValueError, match="not found in data.timeseries"):
        add_solar_pv_block(
            model,
            data,
            solar_pv_params={
                "solar_resource_assignment_by_node_and_profile": {
                    "node_A": {
                        "solar_production__fixed_tilt": "solar_production__does_not_exist"
                    }
                }
            },
        )
