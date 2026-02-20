"""Case configuration schemas for run/playground orchestration."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class EnergyLoadFileConfig:
    """Configuration for energy load CSV parsing."""

    csv_path: Path
    datetime_column: str = "Date"
    # If this exact column is not present, loader can auto-detect a single
    # header containing parenthesized units like "(kW)" or "(kWh)".
    load_column: str = "Electric Demand (kW)"
    datetime_format: str = "%m/%d/%Y %H:%M"


@dataclass(slots=True)
class CaseConfig:
    """Top-level run configuration used by the playground entrypoint."""

    case_name: str
    energy_load: EnergyLoadFileConfig


def default_igiugig_case(project_root: Path) -> CaseConfig:
    """Return the default local case config for Igiugig load data."""
    return CaseConfig(
        case_name="Igiugig",
        energy_load=EnergyLoadFileConfig(
            csv_path=project_root / "data" / "Igiugig" / "Igiugig_Electric_Loads.csv"
        ),
    )


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


def get_case_config(project_root: Path, case_name: str = "igiugig") -> CaseConfig:
    """Return case configuration by case name.

    Case builders are auto-discovered by function name pattern:
    `default_<case_name>_case(project_root)`.
    """
    key = case_name.strip().lower().replace("-", "_").replace(" ", "_")
    fn_name = f"default_{key}_case"
    builder = globals().get(fn_name)

    if callable(builder):
        return builder(project_root)

    available: list[str] = []
    for name, obj in globals().items():
        if not callable(obj):
            continue
        if not (name.startswith("default_") and name.endswith("_case")):
            continue
        available.append(name[len("default_") : -len("_case")])
    available.sort()

    raise ValueError(
        f"Unknown case '{case_name}'. Valid cases: {', '.join(available)}"
    )

