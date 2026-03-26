"""Tests for prorated fixed utility customer charges over the simulation horizon."""

from datetime import datetime, timedelta

import pytest

from data_loading.loaders.utility_rates.customer_charge_horizon import (
    fixed_customer_charges_horizon_usd,
)


def test_daily_multiplies_distinct_days():
    d0 = datetime(2024, 1, 1, 0, 0)
    dts = [d0 + timedelta(hours=h) for h in range(48)]  # two calendar days
    fc = {"first_meter": {"amount": 2.0, "units": "$/day"}}
    assert fixed_customer_charges_horizon_usd(fc, dts) == pytest.approx(4.0)


def test_monthly_full_month_one_charge():
    # Every hour in January 2024 (31 days)
    dts = [datetime(2024, 1, 1, 0, 0) + timedelta(hours=h) for h in range(31 * 24)]
    fc = {"first_meter": {"amount": 310.0, "units": "$/month"}}
    # 310 * (31/31) = 310
    assert fixed_customer_charges_horizon_usd(fc, dts) == pytest.approx(310.0)


def test_monthly_prorated_partial_month():
    # Jan 1 through Jan 15 inclusive: 15 distinct days
    dts = [datetime(2024, 1, 1, 0, 0) + timedelta(hours=h) for h in range(15 * 24)]
    fc = {"first_meter": {"amount": 31.0, "units": "$/month"}}
    # 31 * (15/31) = 15
    assert fixed_customer_charges_horizon_usd(fc, dts) == pytest.approx(15.0)


def test_legacy_minimum_key_ignored():
    """URDB mincharge must not be folded into fixed horizon USD; ignore if present in dict."""
    dts = [datetime(2024, 3, 1, 12, 0) + timedelta(days=i) for i in range(10)]
    fc = {
        "first_meter": {"amount": 1.0, "units": "$/day"},
        "minimum": {"amount": 0.5, "units": "$/day"},
    }
    assert fixed_customer_charges_horizon_usd(fc, dts) == pytest.approx(10.0)


def test_none_datetimes_skipped():
    dts = [datetime(2024, 1, 1, 0, 0), None, datetime(2024, 1, 2, 0, 0)]
    fc = {"first_meter": {"amount": 1.0, "units": "$/day"}}
    assert fixed_customer_charges_horizon_usd(fc, dts) == pytest.approx(2.0)


def test_empty_returns_zero():
    assert fixed_customer_charges_horizon_usd(None, []) == 0.0
    assert fixed_customer_charges_horizon_usd({}, [datetime(2024, 1, 1)]) == 0.0


def test_bad_units_raises():
    dts = [datetime(2024, 1, 1, 0, 0)]
    fc = {"first_meter": {"amount": 5.0, "units": "$/quarter"}}
    with pytest.raises(ValueError, match="Unrecognized fixed-charge units"):
        fixed_customer_charges_horizon_usd(fc, dts)


def test_nonzero_amount_missing_units_raises():
    dts = [datetime(2024, 1, 1, 0, 0)]
    fc = {"first_meter": {"amount": 5.0, "units": ""}}
    with pytest.raises(ValueError, match="non-empty 'units'"):
        fixed_customer_charges_horizon_usd(fc, dts)


def test_invalid_amount_raises():
    dts = [datetime(2024, 1, 1, 0, 0)]
    fc = {"first_meter": {"amount": "not-a-number", "units": "$/day"}}
    with pytest.raises(ValueError, match="invalid amount"):
        fixed_customer_charges_horizon_usd(fc, dts)
