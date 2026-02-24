"""Igiugig case: single-node load data (CSV)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig


def default_igiugig_case(project_root: Path) -> CaseConfig:
    """Return the default local case config for Igiugig load data."""
    return CaseConfig(
        case_name="Igiugig",
        energy_load=EnergyLoadFileConfig(
            csv_path=project_root / "data" / "Igiugig" / "Igiugig_Electric_Loads.csv"
        ),
    )
