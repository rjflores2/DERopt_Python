"""Igiugig case: single-node load data (CSV)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_solar_file


def default_igiugig_case(project_root: Path) -> CaseConfig:
    """Return the default local case config for Igiugig load data."""
    data_dir = project_root / "data" / "Igiugig"
    return CaseConfig(
        case_name="Igiugig",
        energy_load=EnergyLoadFileConfig(
            csv_path=data_dir / "Igiugig_Electric_Loads.csv"
        ),
        solar_path=discover_solar_file(data_dir),
    )
