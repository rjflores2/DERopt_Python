"""Igiugig Multi Node case: multi-node load data (CSV)."""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig


def default_igiugig_multi_node_case(project_root: Path) -> CaseConfig:
    """Return local case config for Igiugig multi-node load data."""
    return CaseConfig(
        case_name="Igiugig Multi Node",
        energy_load=EnergyLoadFileConfig(
            csv_path=project_root
            / "data"
            / "Igiugig_Multi_Node"
            / "Igiugig_Electric_Loads.csv"
        ),
    )
