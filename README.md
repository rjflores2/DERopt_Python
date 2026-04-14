# DERopt Python

Energy system optimization model that selects and sizes distributed energy resources (DERs) to meet electricity needs at lowest cost. Built with **Pyomo** and solved with **Gurobi**.

The model currently includes **grid/utility imports** (energy, demand charges, fixed customer charges when tariff data provides them) and optional **technologies**: solar PV, battery and flow-battery storage, diesel generation, **hydrokinetic** generation (when resource data is loaded), and a hydrogen subsystem (PEM/alkaline electrolyzers, PEM fuel cell, compressed-gas hydrogen storage). Technologies are **opt-in** via `CaseConfig.technology_parameters`; see **[Technology parameters](#technology-parameters)** for exact semantics.

## Quick Start

### Run the model

From the project root:

```bash
python -m run.playground
```

This loads the default case (`Igiugig_xlsx`), loads electricity load data (and solar if the case sets `solar_path`), builds the model, and runs. Output is printed to the console. Unless `DEROPT_QUIET=1`, the run also prints `electricity_load_keys` and `solar_production_keys` so you can author `technology_parameters` with the correct strings.

### Choose a different case

Set the `DEROPT_CASE` environment variable:

```bash
# Windows (PowerShell)
$env:DEROPT_CASE = "igiugig xlsx"; python -m run.playground

# Windows (cmd)
set DEROPT_CASE=igiugig xlsx && python -m run.playground

# Linux / macOS
DEROPT_CASE="igiugig xlsx" python -m run.playground
```

Available cases (auto-discovered from `config/cases/`): `igiugig`, `igiugig multi node`, `igiugig xlsx`, `max capability`.

The **`max capability`** case is a deliberately heavy example (multi-node load, multi-profile solar, battery, diesel, multi-tariff utility, raw price override, financing). It expects data under `data/MaxCapability/` (see `config/cases/max_capability.py`).

## Data

The `data/` directory is **gitignored**; cases point at subfolders you create locally (e.g. `data/Igiugig/`, `data/Igiugig_xlsx/`, `data/MaxCapability/`).

### Electricity load files

- Supported formats: CSV, XLSX, XLS
- Required columns: datetime column (e.g. `Date`), one or more load columns with `(kW)` or `(kWh)` in the header (e.g. `Electric Demand (kW)`). Multiple electricity columns (multi-node) are supported.
- Duplicate load headers are made unique when the file is read (e.g. suffixes on repeated names). Prefer **distinct column names** per node (e.g. `Node 1 Electric (kW)`) so `electricity_load_keys` stay obvious.
- All load data is stored in **kWh** in the model (kW from file is converted using the time step). Series keys: `electricity_load__{suffix}`; list in `data.static["electricity_load_keys"]`.
- Datetime formats: strftime strings, `excel_serial`, `matlab_serial`, or `auto` (auto-detect from numeric values)
- For a data folder, the loader auto-discovers files with `"loads"` in the filename (case-insensitive), preferring `.xlsx` over `.csv` over `.xls`.
- **Time conditioning** (optional): Set `target_interval_minutes=60` or `15` in `EnergyLoadFileConfig` to regularize timestamps when irregular; otherwise only NaN/negative filling is applied. Use `target_interval_minutes=None` (default) to keep native resolution.
- Thermal-looking columns such as `Heating (kW)`, `Cooling (kWh)`, `Thermal Load (kW)`, or `DHW (kW)` are excluded from automatic electricity-load selection so they are not mixed into `electricity_load_keys`.

### Solar resource files (optional)

- When a case has `solar_path` set, the loader reads a solar file (CSV or Excel .xlsx/.xls) and aligns it to the load time vector by time-of-year. Discovery prefers `.xlsx` over `.csv` over `.xls`; the loader supports all three so the discovered path is always loadable.
- The file is treated as **capacity factor** (0–1) per period; stored values are **kWh per kW installed** for that period: CF × `time_step_hours`. Keys: `solar_production__{suffix}`; list in `data.static["solar_production_keys"]`; units in `data.static["solar_production_units"]` = `"kWh/kW"`.

#### Standard solar profile labels

Use consistent column names in solar files so case config (e.g. `max_capacity_area_by_node_and_profile`, `params_by_profile`) can refer to the same keys across sites. Column headers are normalized to a key suffix (lowercase, non-alphanumeric → underscore). Recommended labels:

| Profile | Recommended CSV column | Resulting key |
|--------|------------------------|---------------|
| 1-D tracking | `1D Tracking` or `1d_tracking` | `solar_production__1d_tracking` |
| 2-D tracking | `2D Tracking` or `2d_tracking` | `solar_production__2d_tracking` |
| Fixed, optimal tilt/orientation | `Fixed Optimal` or `fixed_optimal` | `solar_production__fixed_optimal` |
| Fixed south | `Fixed South` or `fixed_south` | `solar_production__fixed_south` |
| Fixed north | `Fixed North` or `fixed_north` | `solar_production__fixed_north` |
| Fixed east | `Fixed East` or `fixed_east` | `solar_production__fixed_east` |
| Fixed west | `Fixed West` or `fixed_west` | `solar_production__fixed_west` |
| Flat (horizontal) | `Flat` or `fixed_flat` | `solar_production__flat` or `solar_production__fixed_flat` |

Use the **resulting key** in config when keying by profile name (e.g. in `max_capacity_area_by_node_and_profile`). Column order defines `solar_production_keys`, which matters if you use a **list** for `params_by_profile` (see below).

### Hydrokinetic resource files (optional)

- When a case sets `hydrokinetic_path` and the loader runs (`run/build_run_data.py`), time series are aligned like solar; `data.static["hydrokinetic_production_keys"]` lists profile keys. See loaders and `technologies/hydrokinetic/` for required static metadata (e.g. reference kW / swept area) when building that block.

## Technology parameters

Technologies are configured under `CaseConfig.technology_parameters`. Config keys must match `technologies.REGISTRY`: `solar_pv`, `battery_energy_storage`, `flow_battery_energy_storage`, `diesel_generator`, `hydrokinetic`, `pem_electrolyzer`, `alkaline_electrolyzer`, `pem_fuel_cell`, `compressed_gas_hydrogen_storage`.

**Semantics (per technology key):**

| Config | Meaning |
|--------|--------|
| **Key omitted** | Do not build that technology (same as leaving it out of the dict). |
| **`None`** | Explicitly do **not** build that technology. |
| **`{}`** | Build the technology using **module defaults** from that package’s `inputs.py` (empty dict still counts as “requested”). |
| **Non-empty `dict`** | Build the technology; merge these values onto the same defaults. |

**Requested technologies and resource data:** If a technology is requested with `{}` or a non-empty dict, `build_model` expects its `register()` hook to attach `model.<technology_name>` (see `model/core.py`). For **resource-dependent** technologies (e.g. `solar_pv` without `solar_production_keys`, `hydrokinetic` without `hydrokinetic_production_keys`), the register function cannot build a block; the run **raises a clear `ValueError`** instead of silently skipping. That is intentional so a requested DER is never dropped without notice. Case builders should only pass `solar_pv` / `hydrokinetic` when the corresponding data was loaded into `DataContainer`, or set the key to `None` / omit it.

Financing for annualized capital on adopted equipment uses `CaseConfig.financials` (`FinancialsConfig`: debt/equity terms). If unset, defaults from `config/case_config.py` apply.

Optional **horizon subsetting** for faster runs: `CaseConfig.time_subset` (`TimeSubsetConfig` in `data_loading/time_subset.py`).

### Solar PV (`solar_pv`)

Technoeconomic parameters (efficiency, capital cost, O&M, area limits, existing PV, recovery on existing) are set via `technology_parameters["solar_pv"]`; defaults live in `technologies/solar_pv/inputs.py`.

For multiple solar profiles, **`params_by_profile`** may be:

- A **list** in the same order as `data.static["solar_production_keys"]` (first list entry = first profile column), or
- A **dict** keyed by the full production key (e.g. `solar_production__fixed_optimal`), so order in the file does not matter.

Area limits use **`max_capacity_area_by_node_and_profile`** (tuple-key dict or nested dict per node). Existing capacity uses **`existing_solar_capacity_by_node_and_profile`**.

```python
technology_parameters={
    "solar_pv": {
        "max_capacity_area_by_node_and_profile": {
            ("electricity_load__a", "solar_production__fixed_optimal"): 500,
            ("electricity_load__a", "solar_production__1d_tracking"): 300,
        },
        # List form (order must match solar_production_keys)
        "params_by_profile": [
            {"efficiency": 0.20, "capital_cost_per_kw": 1500, "om_per_kw_year": 18},
            {"efficiency": 0.22, "capital_cost_per_kw": 2100, "om_per_kw_year": 24},
        ],
        # Or dict form (keys = solar_production__* strings)
        # "params_by_profile": {
        #     "solar_production__fixed_optimal": {"efficiency": 0.20, ...},
        #     "solar_production__1d_tracking": {"efficiency": 0.22, ...},
        # },
    },
}
```

### Battery energy storage (`battery_energy_storage`)

Defaults and validation: `technologies/battery_energy_storage/inputs.py`. Typical keys include charge/discharge efficiency, capital and O&M per kWh, C-rate limits (`max_charge_power_per_kwh`, `max_discharge_power_per_kwh`), optional existing energy capacity per node, and optional `initial_soc_fraction`.

### Diesel generator (`diesel_generator`)

Defaults and validation: `technologies/diesel_generator/inputs.py`.

- **`formulation`** (exact strings): `diesel_lp`, `diesel_binary`, or `diesel_unit_milp`.
- **Fuel economics**: usually `fuel_cost_per_gallon` with heating-value conversion to \$/kWh, or a direct override `fuel_cost_per_kwh_diesel` (do not mix both).
- Optional per-node **existing** capacity / unit counts and **adoption limits** (capacity and discrete units), depending on formulation.

### Hydrogen subsystem (LHV basis)

Hydrogen technologies use **kWh-H2 on a lower heating value basis** (`kWh-H2_LHV`) as the canonical internal unit for hydrogen production, consumption, storage inventory, and hydrogen balance terms. HHV is not used internally.

**Formulation strings** match the diesel pattern: **`<technology>_<model>`** as exact literals (no aliases), e.g. `diesel_lp` for diesel and `pem_electrolyzer_lp` for the PEM electrolyzer.

#### PEM electrolyzer (`pem_electrolyzer`)

Defaults and validation: `technologies/pem_electrolyzer/inputs.py`; block: `technologies/pem_electrolyzer/block.py`.

- Produces hydrogen (`hydrogen_source_term`) and consumes electricity (`electricity_sink_term`).
- **`formulation`** (exact strings): `pem_electrolyzer_lp`, `pem_electrolyzer_binary`, or `pem_electrolyzer_unit_milp`.
- `pem_electrolyzer_binary` big-M uses node peak electric load multiplied by `electrolyzer_binary_big_m_load_multiplier` (default 15) to allow renewable-overbuild charging behavior.
- Uses `electric_to_hydrogen_lhv_efficiency` = (kWh-H2_LHV out)/(kWh-electric in).

#### Alkaline electrolyzer (`alkaline_electrolyzer`)

Defaults and validation: `technologies/alkaline_electrolyzer/inputs.py`; block: `technologies/alkaline_electrolyzer/block.py`.

- Same structure as PEM with different defaults.
- **`formulation`**: `alkaline_electrolyzer_lp`, `alkaline_electrolyzer_binary`, or `alkaline_electrolyzer_unit_milp`.
- Produces hydrogen (`hydrogen_source_term`) and consumes electricity (`electricity_sink_term`).
- Uses the same LHV efficiency convention and binary big-M pattern as PEM.

#### PEM fuel cell (`pem_fuel_cell`)

Defaults and validation: `technologies/pem_fuel_cell/inputs.py`; block: `technologies/pem_fuel_cell/block.py`.

- Consumes hydrogen (`hydrogen_sink_term`) and produces electricity (`electricity_source_term`).
- **`formulation`**: `pem_fuel_cell_lp`, `pem_fuel_cell_binary`, or `pem_fuel_cell_unit_milp`.
- `pem_fuel_cell_binary` big-M is based on node peak electric load.
- Uses `hydrogen_lhv_to_electric_efficiency` = (kWh-electric out)/(kWh-H2_LHV in).

#### Compressed-gas hydrogen storage (`compressed_gas_hydrogen_storage`)

Defaults and validation: `technologies/compressed_gas_hydrogen_storage/inputs.py`; block: `technologies/compressed_gas_hydrogen_storage/block.py`.

- Hydrogen inventory model (analogous to battery SOC, but in kWh-H2_LHV).
- Exposes `hydrogen_sink_term` (charge), `hydrogen_source_term` (discharge).
- Charging incurs compressor electricity via `electricity_sink_term` with coefficient `compression_kwh_electric_per_kwh_h2_lhv`.
- Includes retention/standing-loss and min/max inventory fractions.

### Utility tariffs (single and multi-node)

Utility costs are attached via `utilities/electricity_import_export/`. The grid block is registered when **import energy prices**, **demand charges**, and/or **fixed customer charges** apply for at least one node; otherwise the block may be omitted.

Supported case shapes:

- **Single tariff** via `CaseConfig.utility_rate_path` (OpenEI-style JSON) and/or `CaseConfig.energy_price_path` (raw CSV series for \$/kWh)
- **Multi-tariff** via `CaseConfig.utility_tariffs` with optional node exceptions in `CaseConfig.node_utility_tariff`

When using `utility_tariffs`, do not set the legacy single-tariff fields on `CaseConfig` at the same time (the loader raises).

For multi-tariff:

- `utility_tariffs[0]` is the default tariff
- all nodes use the default unless listed in `node_utility_tariff`
- `node_utility_tariff` should include only exceptions
- repeated tariff definitions are loaded once and reused across nodes

Energy prices are resolved per node and aligned to model timesteps. For OpenEI TOU energy pricing, load datetimes are required. Demand charges (flat/TOU) require both datetimes and `data.static["time_step_hours"]`.

## Adding a new case

1. **Create the case module**  
   Add `config/cases/<case_name>.py` with a `default_<case_name>_case(project_root)` function that returns a `CaseConfig`:

   ```python
   from pathlib import Path
   from config.case_config import CaseConfig, EnergyLoadFileConfig, discover_load_file

   def default_my_island_case(project_root: Path) -> CaseConfig:
       folder = project_root / "data" / "MyIsland"
       load_path = discover_load_file(folder)  # finds *loads*.csv/xlsx
       return CaseConfig(
           case_name="My Island",
           energy_load=EnergyLoadFileConfig(csv_path=load_path),
       )
   ```

2. **Add your data**  
   Place load files in `data/MyIsland/` (or the path you configured).

3. **Run**  
   `DEROPT_CASE=my island python -m run.playground`

No edits to `config/cases/__init__.py` or the playground are required; cases are auto-discovered.

## Conventions: fail hard

The codebase is written to **fail fast with clear errors** instead of allowing silent failures that surface later or downstream.

- **Config vs. missing files:** If case config sets a path (e.g. `solar_path`, `utility_rate_path`) and that path does not exist, the run **raises** (e.g. `FileNotFoundError`) with a message that includes the path. We do not silently skip or set a value to `None` when the user has asked for that file.
- **Data validation:** Loaders and the model validate required fields (e.g. `electricity_load_keys`, `time`, `time_serial`) and **raise** with a clear message if something is missing. The model calls `data.validate_minimum_fields()` at entry so invalid data never propagates into the build.
- **Loader and API contracts:** When a function is required to return a specific type (e.g. `ParsedRate` from a utility loader), we check the return value and **raise** with a descriptive error if it is `None` or the wrong type, rather than passing it downstream.
- **Defensive checks:** For example, if `build_model` is called with data and returns `None`, the playground raises instead of continuing; technology parameter validation (e.g. solar efficiency in (0, 1]) raises so bad config is caught at build time.
- **Requested registry technologies:** If `technology_parameters` includes a key with value **`{}` or a config dict** (not `None`), the technology’s `register()` must attach **`model.<technology_name>`** and return that same block; otherwise `build_model` raises. Conditional technologies must not appear in `technology_parameters` unless the run data supports them (see [Technology parameters](#technology-parameters)).

When adding or changing code, prefer **explicit validation and early raises** over defaulting or continuing with invalid or missing data.

## Project structure

| Path | Role |
|------|------|
| `config/` | Run and case configuration; `config/cases/` holds one module per case |
| `data/` | Local input data (gitignored); load profiles, rates, etc. |
| `data_loading/` | Loaders that read data and populate the DataContainer |
| `model/` | Pyomo model assembly (`core.py` is the central meeting place) |
| `technologies/` | Technology modules (solar, storage, diesel, hydrokinetic, hydrogen subsystem); `REGISTRY` in `technologies/__init__.py` |
| `utilities/` | Grid/tariff; `electricity_import_export/` mirrors tech packages (`block`, `inputs`, `demand_charge_indexing`, diagnostics) |
| `run/playground.py` | Main entry point |
| `shared/` | Shared utilities (e.g. financials) |

## Dependencies

Declared in **`pyproject.toml`** (install with `pip install -e .`):

- Python 3.10+
- **Pyomo**, **gurobipy** — model and solver
- **pandas**, **openpyxl**, **xlrd** — CSV/Excel loading
- **numpy**, **scipy** — numerics

Development tests: `pip install -e ".[dev]"` (adds **pytest**).

Other libraries (e.g. plotting, notebooks, YAML, DB drivers) are not required for the core package as currently specified; add them in your environment if you use tooling that depends on them.

## For developers

- **Where to add cases, loaders, resources; how to run tests:** `docs/DEVELOPMENT.md`
- **Install in editable mode:** `pip install -e ".[dev]"` then `pytest`

## More information

- **Implementation plan and roadmap**: `docs/PLAN.md`
- **Requirements spec**: `requirements/deropt_rebuild_spec.md`
