"""Case configuration schemas for run/playground orchestration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_loading.time_subset import TimeSubsetConfig


@dataclass(slots=True)
class FinancialsConfig:
    """User-configurable debt/equity financing for capital amortization.

    Used by shared.financials to compute the annual payment factor. All fields
    can be set in config (or case builder) so users can change financing assumptions.
    """

    debt_fraction: float = 0.5       # Fraction of capital from debt (0..1)
    debt_years: float = 10.0        # Debt payback period (years)
    debt_rate: float = 0.08         # Debt interest rate (e.g. 0.08 = 8%)
    equity_years: float = 5.0       # Equity payback period (years)
    equity_rate: float = 0.15       # Equity return rate (e.g. 0.15 = 15%)
    # Years over which to levelize the equivalent annual payment (default: max of debt/equity years)
    levelization_years: float | None = None  # None = use max(debt_years, equity_years)

_LOAD_EXTENSIONS = (".csv", ".xlsx", ".xls")
_LOAD_PATTERN = "loads"
_SOLAR_PATTERN = "solar"
# Hydrokinetic resource files: stem contains one of these (case-insensitive), e.g. hkt_potential.xlsx
_HKT_STEM_SUBSTRINGS = ("hkt", "hydrokinetic")


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
    # Optional resource profile files (e.g. solar.csv). Path only; loader infers format.
    solar_path: Path | None = None
    # Optional hydrokinetic profiles: one column per river location (.csv / .xlsx / .xls).
    hydrokinetic_path: Path | None = None
    # HKT files: power (kW) per timestamp; divide by this before × time_step_hours → kWh/kW (see loader).
    hydrokinetic_reference_kw: float = 1.0
    # If set, use this column as timestamps; else auto-detect like solar/HKT loader.
    hydrokinetic_datetime_column: str | None = None
    # m² of the reference device used to produce the HKT resource file (scales kWh/kW → kWh/m²).
    hydrokinetic_reference_swept_area_m2: float | None = None
    # Optional technology parameters by technology name, e.g. {"solar_pv": {...}}.
    # Values override technology defaults defined in each technology module.
    technology_parameters: dict[str, dict[str, Any]] | None = None
    # Financing assumptions for capital amortization (debt/equity). User-editable.
    financials: FinancialsConfig | None = None  # None = use FinancialsConfig() defaults
    # Optional utility rate (OpenEI-style JSON). When set, load via load_openei_rate for grid cost, etc.
    utility_rate_path: Path | None = None
    # When the JSON has multiple "items", which one to use (0-based). None = first.
    utility_rate_item_index: int | None = None
    # Optional raw 8760/N energy price file (CSV). Use for wholesale, real-time, etc.
    # If set, this is used for the import price vector; utility_rate_path can still be set for demand charges/metadata.
    energy_price_path: Path | None = None
    # CSV column name for price ($/kWh). If None, first numeric column or only column is used.
    energy_price_column: str | None = None
    # Optional reduced-horizon run mode for faster development iterations.
    time_subset: TimeSubsetConfig | None = None
    # Optional multi-tariff model:
    # - first entry in utility_tariffs is the default tariff
    # - all nodes use default unless overridden in node_utility_tariff
    utility_tariffs: list["UtilityTariffConfig"] | None = None
    # Optional overrides: node key -> tariff_key. Only specify exceptions from the default.
    node_utility_tariff: dict[str, str] | None = None


@dataclass(slots=True)
class UtilityTariffConfig:
    """Tariff source bundle used by per-node utility assignment.

    Supported combinations per tariff:
    - OpenEI only (utility_rate_path set, energy_price_path unset)
    - raw only (energy_price_path set, utility_rate_path unset)
    - OpenEI + raw override (both set; raw overrides energy prices only)
    """

    tariff_key: str
    utility_rate_path: Path | None = None
    utility_rate_item_index: int | None = None
    energy_price_path: Path | None = None
    energy_price_column: str | None = None


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


def discover_solar_file(folder: Path) -> Path | None:
    """Find first csv/xlsx/xls file with 'solar' in name (case-insensitive).

    Prefer xlsx > csv > xls. Returns None if no match (optional resource).
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    candidates: list[Path] = []
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in _LOAD_EXTENSIONS:
            continue
        if _SOLAR_PATTERN.lower() not in f.stem.lower():
            continue
        candidates.append(f)
    if not candidates:
        return None
    for ext in (".xlsx", ".csv", ".xls"):
        for c in candidates:
            if c.suffix.lower() == ext:
                return c
    return candidates[0]


def discover_hydrokinetic_file(folder: Path) -> Path | None:
    """Find first csv/xlsx/xls whose stem contains 'hkt' or 'hydrokinetic' (case-insensitive).

    Prefer xlsx > csv > xls. Returns None if no match (optional resource).
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    candidates: list[Path] = []
    stem_lower: str
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in _LOAD_EXTENSIONS:
            continue
        stem_lower = f.stem.lower()
        if not any(s in stem_lower for s in _HKT_STEM_SUBSTRINGS):
            continue
        candidates.append(f)
    if not candidates:
        return None
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

