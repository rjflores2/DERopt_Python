"""SCE OpenEI loader: customer fixed charges from JSON fields."""

from data_loading.loaders.utility_rates import load_openei_rate


def test_extract_customer_fixed_charges_via_minimal_item():
    item = {
        "utility": "Southern California Edison Co",
        "name": "Test",
        "energyratestructure": [[{"rate": 0.1}]],
        "energyweekdayschedule": [[0] * 24 for _ in range(12)],
        "energyweekendschedule": [[0] * 24 for _ in range(12)],
        "fixedchargefirstmeter": 702.22,
        "fixedchargeunits": "$/month",
        "mincharge": 1.5,
        "minchargeunits": "$/day",
    }
    r = load_openei_rate(item)
    assert r.customer_fixed_charges is not None
    assert r.customer_fixed_charges["first_meter"]["amount"] == 702.22
    assert r.customer_fixed_charges["first_meter"]["units"] == "$/month"
    assert r.customer_fixed_charges["minimum"]["amount"] == 1.5
    assert r.customer_fixed_charges["minimum"]["units"] == "$/day"


def test_sce_d_tou_and_gs3_fixed_charges_differ():
    """Compare two real tariff files when present under data/ (local / CI with data)."""
    from pathlib import Path

    d_path = Path(__file__).resolve().parents[1] / "data" / "Igiugig_xlsx" / "SCE_D_TOU.json"
    g_path = Path(__file__).resolve().parents[1] / "data" / "Igiugig_xlsx" / "SCE_GS3_TOU.json"
    if not d_path.is_file() or not g_path.is_file():
        import pytest

        pytest.skip("SCE JSON fixtures not in data/Igiugig_xlsx")

    d = load_openei_rate(d_path)
    g = load_openei_rate(g_path)

    assert d.customer_fixed_charges is not None
    assert g.customer_fixed_charges is not None

    # D-TOU: small $/day first meter + minimum from sample JSON
    assert d.customer_fixed_charges["first_meter"]["units"] == "$/day"
    assert d.customer_fixed_charges["first_meter"]["amount"] == 0.031
    assert "minimum" in d.customer_fixed_charges

    # GS-3: larger $/month first meter; sample had no mincharge key
    assert g.customer_fixed_charges["first_meter"]["units"] == "$/month"
    assert g.customer_fixed_charges["first_meter"]["amount"] == 702.22

    assert d.demand_charges is None
    assert g.demand_charges is not None
    assert g.demand_charges.get("demand_charge_type") == "both"
