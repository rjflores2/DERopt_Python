"""Igiugig xlsx case: single-node load data (xlsx, auto-discovered)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_load_file


def default_igiugig_xlsx_case(project_root: Path) -> CaseConfig:
    """Return case config for Igiugig xlsx load data (auto-discovers load file)."""
    folder = project_root / "data" / "Igiugig_xlsx"
    load_path = discover_load_file(folder)
    return CaseConfig(
        case_name="Igiugig xlsx",
        energy_load=EnergyLoadFileConfig(csv_path=load_path),
    )
