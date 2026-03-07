"""Igiugig case: single-node load data (CSV)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_solar_file


def default_igiugig_case(project_root: Path) -> CaseConfig:
    """Return the default local case config for Igiugig load data."""
    data_dir = project_root / "data" / "Igiugig"
    rate_path = data_dir / "SCE_D_TOU.json"
    if not rate_path.is_file():
        rate_path = project_root / "data" / "Igiugig_xlsx" / "SCE_D_TOU.json"
    if not rate_path.is_file():
        rate_path = None
    return CaseConfig(
        case_name="Igiugig",
        energy_load=EnergyLoadFileConfig(
            csv_path=data_dir / "Igiugig_Electric_Loads.csv"
        ),
        solar_path=discover_solar_file(data_dir),
        technology_parameters={"solar_pv": {}},
        utility_rate_path=rate_path,
    )
