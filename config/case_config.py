"""Case configuration schemas for run/playground orchestration."""

from dataclasses import dataclass
from pathlib import Path

_LOAD_EXTENSIONS = (".csv", ".xlsx", ".xls")
_LOAD_PATTERN = "loads"


@dataclass(slots=True)
class EnergyLoadFileConfig:
    """Configuration for energy load file parsing. Supports .csv, .xlsx, .xls."""

    csv_path: Path  # Path to .csv, .xlsx, or .xls file
    sheet_name: int | str = 0  # Excel sheet (0 = first sheet); ignored for CSV
    datetime_column: str = "Date"
    # If this exact column is not present, loader can auto-detect a single
    # header containing parenthesized units like "(kW)" or "(kWh)".
    load_column: str = "Electric Demand (kW)"
    # Datetime column interpretation (optional; default None = auto-detect):
    # - None or "auto" = infer MATLAB vs Excel serial by magnitude for numeric; native datetime passed through; text tries common formats.
    # - A strftime string (e.g. "%m/%d/%Y %H:%M") = parse text dates.
    # - "matlab_serial" / "excel_serial" = column is numeric serial date.
    datetime_format: str | None = None
    # Time conditioning: regularize timestamps and fill gaps.
    # - None  = do not change the time grid (no resampling)
    # - 60    = target hourly grid
    # - 15/30 = target 15/30-minute grid, etc.
    target_interval_minutes: int | None = None
    interpolation_method: str = "linear"  # for filling NaN (linear, time, nearest)
    treat_negative_as_missing: bool = True  # replace negative load with NaN before interpolate
    # Resample only when timestamps differ significantly from target grid. If timestamps are
    # within tolerance of a regular grid, skip resampling and only fill NaN/negative.
    resample_only_if_irregular: bool = True  # True = resample only when needed
    resample_tolerance_seconds: float = 60.0  # consider "regular" if within this of target grid


@dataclass(slots=True)
class CaseConfig:
    """Top-level run configuration used by the playground entrypoint."""

    case_name: str
    energy_load: EnergyLoadFileConfig


def discover_load_file(folder: Path) -> Path:
    """Find first csv/xls/xlsx file with 'loads' in name (case-insensitive).

    Prefer xlsx > csv > xls (xls is legacy Excel 97-2003). Raises FileNotFoundError if no match.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    candidates: list[Path] = []
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in _LOAD_EXTENSIONS:
            continue
        if _LOAD_PATTERN.lower() not in f.stem.lower():
            continue
        candidates.append(f)
    if not candidates:
        raise FileNotFoundError(
            f"No load files (csv/xls/xlsx with 'loads' in name) found in {folder}"
        )
    for ext in (".xlsx", ".csv", ".xls"):
        for c in candidates:
            if c.suffix.lower() == ext:
                return c
    return candidates[0]


def get_case_config(project_root: Path, case_name: str = "igiugig") -> CaseConfig:
    """Return case configuration by case name.

    Case builders live in config/cases/ (one module per case) and are
    auto-discovered by function name pattern: default_<case_name>_case(project_root).
    """
    import config.cases as cases_module

    key = case_name.strip().lower().replace("-", "_").replace(" ", "_")
    fn_name = f"default_{key}_case"
    builder = getattr(cases_module, fn_name, None)

    if callable(builder):
        return builder(project_root)

    available: list[str] = []
    for name in dir(cases_module):
        if name.startswith("default_") and name.endswith("_case"):
            available.append(name[len("default_") : -len("_case")])
    available.sort()

    raise ValueError(
        f"Unknown case '{case_name}'. Valid cases: {', '.join(available)}"
    )
