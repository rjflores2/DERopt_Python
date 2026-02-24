# DERopt Python

Energy system optimization model that selects and sizes distributed energy resources (DERs) to meet electricity and energy needs at lowest cost. Built with **Pyomo** and solved with **Gurobi**.

## Quick Start

### Run the model

From the project root:

```bash
python -m run.playground
```

This loads the default case (`igiugig`), loads energy demand data, builds the model, and runs. Output is printed to the console.

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

### Energy load files

- Supported formats: CSV, XLSX, XLS
- Required columns: datetime column (e.g. `Date`), load column (e.g. `Electric Demand (kW)`)
- Datetime formats: strftime strings, `excel_serial`, `matlab_serial`, or `auto` (auto-detect from numeric values)
- For a data folder, the loader auto-discovers files with `"loads"` in the filename (e.g. `Electric_Loads.xlsx`)
- **Time conditioning** (optional): Set `target_interval_minutes=60` or `15` in `EnergyLoadFileConfig` to regularize timestamps to an hourly or 15-minute grid, fill NaN via interpolation, and treat negative values as missing. Use `target_interval_minutes=None` (default) to pass through raw data.

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
- **Requirements spec**: `requirements/deropt_rebuild_spec.md`
- **Extending the model**: `docs/extending_the_model.md`
