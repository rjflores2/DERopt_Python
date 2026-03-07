"""Igiugig xlsx case: single-node load data (xlsx, auto-discovered)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_load_file, discover_solar_file


def default_igiugig_xlsx_case(project_root: Path) -> CaseConfig:
    """Return case config for Igiugig xlsx load data (auto-discovers load file)."""
    folder = project_root / "data" / "Igiugig_xlsx"
    load_path = discover_load_file(folder)
    # Optional: point to an OpenEI rate JSON in this folder (e.g. SCE_D_TOU.json, SCE_D_Tiered.json).
    rate_path = folder / "SCE_D_TOU.json"
    if not rate_path.is_file():
        rate_path = None
    return CaseConfig(
        case_name="Igiugig xlsx",
        energy_load=EnergyLoadFileConfig(csv_path=load_path),
        solar_path=discover_solar_file(folder),
        technology_parameters={"solar_pv": {}},
        utility_rate_path=rate_path,
    )
