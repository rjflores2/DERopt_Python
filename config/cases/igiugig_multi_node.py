"""Igiugig Multi Node case: multi-node load data (CSV)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_solar_file


def default_igiugig_multi_node_case(project_root: Path) -> CaseConfig:
    """Return local case config for Igiugig multi-node load data."""
    data_dir = project_root / "data" / "Igiugig_Multi_Node"
    return CaseConfig(
        case_name="Igiugig Multi Node",
        energy_load=EnergyLoadFileConfig(
            csv_path=data_dir / "Igiugig_Electric_Loads.csv"
        ),
        solar_path=discover_solar_file(data_dir),
    )
