"""Tests for shared equipment / O&M diagnostic helpers."""

from technologies.equipment_cost_diagnostics import equipment_capital_om_warnings


def test_negative_capital_warns():
    w = equipment_capital_om_warnings(
        "Tech X", -1.0, 5.0, capital_name="capex", om_name="om"
    )
    assert len(w) == 1
    assert "negative" in w[0].lower()


def test_both_zero_warns():
    w = equipment_capital_om_warnings("Tech X", 0.0, 0.0, capital_name="capex", om_name="om")
    assert len(w) == 1
    assert "both zero" in w[0].lower()


def test_normal_costs_silent():
    assert equipment_capital_om_warnings("Tech X", 100.0, 2.0, capital_name="c", om_name="o") == []
