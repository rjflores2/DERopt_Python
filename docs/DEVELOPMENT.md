# For developers

Short reference so you can find things and extend the code without hunting.

## Entry point

- **Run a case:** `python -m run.playground` (default case from `DEROPT_CASE` env, or `Igiugig_xlsx`).
- **Orchestration:** `run/playground.py` gets config → `run/build_run_data.py` fills one `DataContainer` → `model/core.build_model(data)` returns the Pyomo model.

## Where to add things

| Want to… | Do this |
|----------|--------|
| **Add a new case** | Add `config/cases/<name>.py` with `def default_<name>_case(project_root: Path) -> CaseConfig`. Return a `CaseConfig`; no need to edit `__init__.py`—cases are auto-discovered by naming convention. |
| **Add a new utility rate loader** | In `data_loading/loaders/utility_rates/` add a module (e.g. `pge.py`). Implement a loader that takes the rate item dict and returns a `ParsedRate`. Decorate it with `@register_utility("Utility Name")`. It will be picked up on import. |
| **Add a new resource (e.g. wind)** | In `run/build_run_data.py`, add a branch after solar: load the file, merge into `data.timeseries` (and `data.static` keys) so the container stays the single source of truth. Add the corresponding technology block in `technologies/` and register it. |
| **Subset the time horizon** | Set `case_cfg.time_subset = TimeSubsetConfig(months=[1,2], max_steps=744)` (or `iso_weeks=...`) in your case builder. Subsetting runs inside `build_run_data` after all loaders; it slices every per-timestep series including `data.import_prices`. |

## Data flow

1. **Config** (`config/case_config.py`, `config/cases/*.py`): `CaseConfig` holds paths and options (load, solar, utility rate, time subset, technology params, financials).
2. **Data** (`run/build_run_data.py`): Builds one `DataContainer`: load → solar → utility (import prices + optional rate) → time subset. All per-timestep data (load, solar, import_prices) live in or on that container and stay aligned.
3. **Model** (`model/core.py`): `build_model(data)` reads from `data` only (no separate rate/price args). It creates the time set, nodes, attaches technology blocks from the registry, and builds the balance constraints.

## Tests

From project root:

```bash
pytest
```

If you use `pip install -e .`, the package is installed in editable mode so imports resolve and tests can run without `PYTHONPATH` hacks.

## Demand charge data (OpenEI to utility block)

The SCE loader puts demand into `ParsedRate.demand` for the model utility block:

- **demand_type:** `"flat"` | `"tou"` | `"both"`.
- **Flat:** `flatdemandstructure` (rate $/kW), `flat_demand_applicable_months` (month indices 0–11). The block adds one peak variable per applicable month and cost = rate × max demand in that month.
- **TOU:** `demandratestructure` (tiers with rate $/kW), `demandweekdayschedule` / `demandweekendschedule` (12×24: schedule[month][hour] = tier). The block uses run `datetimes` to map each t to a tier and adds one peak variable per tier.

So the loader output is what the utility block expects; no extra demand processing is required.

## Conventions

- **Fail fast:** If config points to a file that doesn’t exist, or data is missing a required field, raise with a short message and the path/key. Don’t silently skip or default.
- **One container:** Load, solar, utility (import prices, optional rate), and any future resources are all on `DataContainer`. The model only sees `data`.
- **Single extension point for “run data”:** All loading and subsetting is in `build_run_data` so adding wind, hydro, or export rates doesn’t bloat the entry script.
