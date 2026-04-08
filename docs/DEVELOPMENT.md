# For developers

Short reference so you can find things and extend the code without hunting. For milestones and roadmap, see **`docs/PLAN.md`**. For running cases and data formats, see **`README.md`**.

## Entry point

- **Run a case:** `python -m run.playground`  
  Default case name comes from `DEROPT_CASE` (default in code: `Igiugig_xlsx` if unset).
- **Pipeline:** `run/playground.py` → `get_case_config` → `build_run_data` → `build_model` → (optional) Gurobi → `extract_solution` / diagnostics.

Unless `DEROPT_QUIET=1`, the playground prints **`electricity_load_keys`** and **`solar_production_keys`** after load—use those exact strings in `technology_parameters` (e.g. area limits, per-node maps).

## Where to add things

| Want to… | Do this |
|----------|--------|
| **Add a new case** | Add `config/cases/<name>.py` with `def default_<name>_case(project_root: Path) -> CaseConfig`. The function name must follow that pattern (normalized case name: spaces/ hyphens → underscores). No edits to `config/cases/__init__.py` required. |
| **Add a new technology** | Add a package under `technologies/<name>/` with `register(model, data, *, technology_parameters, financials)` that attaches a Pyomo `Block`. Append `("<config_key>", register)` to **`technologies.REGISTRY`** in `technologies/__init__.py`. Defaults and validation live in `inputs.py` beside the block. Optional: `collect_equipment_cost_diagnostics(model, data, case_cfg)` for `utilities.model_diagnostics`. |
| **Add a new utility rate parser** | In `data_loading/loaders/utility_rates/`, add a module (e.g. `pge.py`). Implement a loader that takes the OpenEI **rate item** dict and returns **`ParsedRate`**. Decorate with `@register_utility("Utility Display Name")`. Import side effects register the parser. |
| **Add a new time series resource (e.g. wind)** | Prefer extending **`run/build_run_data.py`** after solar: load file, write into `data.timeseries` and `data.static` keys, keep lengths equal to `len(data.indices["time"])`. Add a technology block and **`REGISTRY`** entry. |
| **Subset the time horizon** | Set `case_cfg.time_subset = TimeSubsetConfig(months=[1, 2], max_steps=744)` and/or `iso_weeks=...` in the case builder. Subsetting runs at the end of `build_run_data` and slices aligned series (including **`import_prices`** and **`import_prices_by_node`**). |
| **Per-node tariffs** | Use **`CaseConfig.utility_tariffs`** (list of **`UtilityTariffConfig`**) and optional **`node_utility_tariff`** map from node key → `tariff_key`. Do not set legacy `utility_rate_path` / `energy_price_path` on `CaseConfig` when using `utility_tariffs` (the loader raises if both are present). |

## Data flow

1. **`CaseConfig`** (`config/case_config.py`, built in `config/cases/*.py`): paths for load and optional solar, optional `technology_parameters`, `financials`, `time_subset`, utility fields (single-tariff **or** `utility_tariffs` bundle).

2. **`build_run_data`** (`run/build_run_data.py`):  
   `load_energy_load` → optional `load_solar_into_container` → resolve utility(ies) into **`import_prices`** / **`import_prices_by_node`**, **`utility_rate`** / **`utility_rate_by_node`**, **`node_utility_tariff_key`** → optional **`apply_time_subset`**.

3. **`build_model`** (`model/core.py`):  
   Validates series lengths, attaches **`model.import_prices_by_node`** and **`model.utility_rate_by_node`**, iterates **`technologies.REGISTRY`** (skips keys where `technology_parameters[k] is None`), then calls **`utilities.electricity_import_export.register`**. Builds electricity balance and objective from block contributions.

## Technology registry

Configured keys today (see **`technologies/__init__.py`**):

| Config key | Package |
|------------|---------|
| `solar_pv` | `technologies/solar_pv/` |
| `battery_energy_storage` | `technologies/battery_energy_storage/` |
| `diesel_generator` | `technologies/diesel_generator/` |

The grid block is **not** in this registry; it is always registered from **`model.core`** via **`utilities.electricity_import_export`**, subject to whether prices/charges apply.

## Tests

From project root:

```bash
pip install -e ".[dev]"
pytest
```

`pyproject.toml` sets `pythonpath = ["."]` for pytest so imports resolve without manual `PYTHONPATH`.

## ParsedRate and the utility block

Loaders return **`ParsedRate`** (`data_loading/loaders/utility_rates/openei_router.py`). Relevant fields:

### Energy (TOU)

- **`rate_type`** includes `"tou"` for time-of-use energy; schedule and prices live in **`payload`** (SCE maps these for `get_import_prices_for_timestamps`).

### Demand charges — **`demand_charges`** dict (not `demand`)

When present, normalized keys include:

- **`demand_charge_type`:** `"flat"` | `"tou"` | `"both"`.
- **TOU:** `demand_charge_ratestructure`, `demand_charge_weekdayschedule`, `demand_charge_weekendschedule` (12×24: `[month][hour]` = tier index into the rate structure).
- **Flat:** `flat_demand_charge_structure`, `flat_demand_charge_months`, `flat_demand_charge_applicable_months` (month indices 0–11).

The utility block and **`demand_charge_indexing.py`** map run **`datetimes`** and **`time_step_hours`** to billing windows and peak proxy variables. See **`data_loading/loaders/utility_rates/sce.py`** for how OpenEI fields map into this shape.

### Fixed and minimum charges

- **`customer_fixed_charges`** — true fixed customer charges (e.g. first meter); horizon USD via **`customer_charge_horizon.fixed_customer_charges_horizon_usd`** and included in the utility objective when applicable.
- **`minimum_meter_charge`** — metadata (minimum bill); **not** the same as daily/monthly fixed charges; treated separately from fixed horizon charges.

## Conventions

- **Fail fast:** Missing files, unknown keys in `node_utility_tariff`, length mismatches, or invalid parameters should **raise** with a short message.
- **One container:** After `build_run_data`, **`DataContainer`** is the single source for series and static metadata the model consumes (plus attributes like **`import_prices_by_node`**).
- **Single place for run data assembly:** Prefer extending **`build_run_data`** rather than growing **`playground.py`**.
