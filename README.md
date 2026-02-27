# DERopt Python

Energy system optimization model that selects and sizes distributed energy resources (DERs) to meet electricity and energy needs at lowest cost. Built with **Pyomo** and solved with **Gurobi**.

## Quick Start

### Run the model

From the project root:

```bash
python -m run.playground
```

This loads the default case (`igiugig_multi_node`), loads electricity load data (and solar if present for the case), builds the model, and runs. Output is printed to the console.

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

Available cases (as of this writing): `igiugig`, `igiugig multi node`, `igiugig xlsx`.

## Data

Input data lives in the `data/` folder (gitignored). Each case points to a subfolder, e.g. `data/Igiugig/`, `data/Igiugig_xlsx/`.

### Electricity load files

- Supported formats: CSV, XLSX, XLS
- Required columns: datetime column (e.g. `Date`), one or more load columns with `(kW)` or `(kWh)` in the header (e.g. `Electric Demand (kW)`). Multiple columns (e.g. multi-node) are supported; duplicate headers are deduplicated.
- All load data is stored in **kWh** in the model (kW from file is converted using the time step). Series keys: `electricity_load__{suffix}`; list in `data.static["electricity_load_keys"]`.
- Datetime formats: strftime strings, `excel_serial`, `matlab_serial`, or `auto` (auto-detect from numeric values)
- For a data folder, the loader auto-discovers files with `"loads"` in the filename (e.g. `Electric_Loads.xlsx`)
- **Time conditioning** (optional): Set `target_interval_minutes=60` or `15` in `EnergyLoadFileConfig` to regularize timestamps when irregular; otherwise only NaN/negative filling is applied. Use `target_interval_minutes=None` (default) to keep native resolution.

### Solar resource files (optional)

- When a case has `solar_path` set, the loader reads a solar CSV and aligns it to the load time vector by time-of-year.
- Output is **kWh per kW capacity** (kWh/kW): capacity factor from file × time step. Keys: `solar_production__{suffix}`; list in `data.static["solar_production_keys"]`; units in `data.static["solar_production_units"]` = `"kWh/kW"`.

### Technology parameters (solar)

Solar technoeconomic parameters (efficiency, capital cost, O&M, area limits (per profile)) are set via case config’s `technology_parameters["solar_pv"]`; defaults live in `technologies/solar_pv.py`. When you have multiple solar profiles (e.g. fixed and 1-D tracking), give each its own values by setting **`params_by_profile`** to a **list in the same order as your solar data columns** (first list entry = first profile, second = second, etc.). Each profile can have its own area limit via **`max_capacity_area_by_profile`** (list in SOLAR order)—e.g. south-facing vs east-facing roof area:

```python
# Example: two profiles (e.g. fixed, then 1-D tracking) — order must match solar_production_keys
technology_parameters={
    "solar_pv": {
        "max_capacity_area_by_profile": [500, 300],  # area limit per profile (e.g. south roof, east roof)
        "params_by_profile": [
            {"efficiency": 0.20, "capital_cost_per_kw": 1500, "om_per_kw_year": 18},
            {"efficiency": 0.22, "capital_cost_per_kw": 2100, "om_per_kw_year": 24},
        ],
    },
}
```

You can override only some fields per profile; the rest come from the top-level `solar_pv` dict or from defaults. Existing capacity is per (node, profile): set **`existing_solar_capacity_by_node_and_profile`** to `{(node_key, profile_key): kW}` or `{node_key: {profile_key: kW}}`; default is 0 at every node.

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

## Project structure

| Path | Role |
|------|------|
| `config/` | Run and case configuration; `config/cases/` holds one module per case |
| `data/` | Input data (gitignored); load profiles, rates, etc. |
| `data_loading/` | Loaders that read data and populate the DataContainer |
| `model/` | Pyomo model assembly (`core.py` is the central meeting place) |
| `technologies/` | Technology modules (PV, wind, batteries, etc.) |
| `utilities/` | Grid/tariff and network (import/export, multi-node) |
| `run/playground.py` | Main entry point |
| `shared/` | Shared utilities (e.g. financials) |

## Dependencies

- Python 3.10+
- **Pyomo**, **gurobipy** - Optimization model and solver
- **pandas**, **openpyxl**, **xlrd** - Data loading (CSV, Excel)
- **numpy**, **scipy** - Numerics
- **networkx** - Graph/network structures (multi-node)
- **matplotlib**, **plotly** - Plotting and visualization
- **jupyter** - Interactive analysis
- **pint** - Unit handling
- **sympy** - Symbolic math (if needed)
- **pyyaml** - YAML config loading
- **pymysql**, **pyodbc** - Database connections (if needed)
- **pyro4** - Remote/distributed (if needed)

## More information

- **Implementation plan**: `docs/deropt_python_pyomo_rebuild.md`
- **Concrete slice steps**: `docs/implementation_slices_detailed.md`
- **Requirements spec**: `requirements/deropt_rebuild_spec.md`
- **Extending the model**: `docs/extending_the_model.md`
