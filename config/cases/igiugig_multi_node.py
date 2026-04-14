"""Igiugig Multi Node case: multi-node load data (CSV)."""

from pathlib import Path

from config.case_config import (
    CaseConfig,
    EnergyLoadFileConfig,
    discover_hydrokinetic_file,
    discover_solar_file,
)


def default_igiugig_multi_node_case(project_root: Path) -> CaseConfig:
    """Return local case config for Igiugig multi-node load data."""
    data_dir = project_root / "data" / "Igiugig_Multi_Node"
    rate_path = data_dir / "SCE_D_TOU.json"
    if not rate_path.is_file():
        rate_path = None
    solar_path = discover_solar_file(data_dir)
    tech_params = None
    if solar_path is not None:
        tech_params = {
            "solar_pv": {
                "params_by_profile": [
                    {"efficiency": 0.20, "capital_cost_per_kw": 1500.0, "om_per_kw_year": 18.0},
                    {"efficiency": 0.22, "capital_cost_per_kw": 2100.0, "om_per_kw_year": 24.0},
                ],
                "max_capacity_area_by_node_and_profile": {
                    "electricity_load__node_1": {"solar_production__fixed_kw_kw": 120.0, "solar_production__1d_tracking_kw_kw": 80.0},
                    "electricity_load__node_2": {"solar_production__fixed_kw_kw": 95.0, "solar_production__1d_tracking_kw_kw": 60.0},
                    "electricity_load__node_3": {"solar_production__fixed_kw_kw": 150.0, "solar_production__1d_tracking_kw_kw": 100.0},
                },
            },
        }
    return CaseConfig(
        case_name="Igiugig Multi Node",
        energy_load=EnergyLoadFileConfig(
            csv_path=data_dir / "Igiugig_Electric_Loads.csv"
        ),
        solar_path=solar_path,
        hydrokinetic_path=discover_hydrokinetic_file(data_dir),
        utility_rate_path=rate_path,
        technology_parameters=tech_params,
    )
