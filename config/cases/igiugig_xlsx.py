"""Igiugig xlsx case: single-node load data (xlsx, auto-discovered)."""

from pathlib import Path

from config.case_config import (
    CaseConfig,
    EnergyLoadFileConfig,
    discover_hydrokinetic_file,
    discover_load_file,
    discover_solar_file,
)


def default_igiugig_xlsx_case(project_root: Path) -> CaseConfig:
    """Return case config for Igiugig xlsx load data (auto-discovers load file)."""
    folder = (project_root / "data" / "Igiugig_xlsx").resolve()
    load_path = discover_load_file(folder)
    # Domestic TOU (no demand charges); use SCE_GS3_TOU.json for demand charges.
    rate_path = folder / "SCE_D_TOU.json"
    solar_path = discover_solar_file(folder)
    tech_params = {"solar_pv": {}} if solar_path is not None else None
    return CaseConfig(
        case_name="Igiugig xlsx",
        energy_load=EnergyLoadFileConfig(csv_path=load_path),
        solar_path=solar_path,
        hydrokinetic_path=discover_hydrokinetic_file(folder),
        technology_parameters=tech_params,
        utility_rate_path=rate_path,
    )
