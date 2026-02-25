# Implementation Slices — Concrete Steps

This document refines the high-level slices from `deropt_python_pyomo_rebuild.md` into a list of **concrete, ordered steps** per slice. Use this for task breakdown, PR scope, and regression gates.

---

## Dependencies and parallel work

Several slices only make sense when built **together or in tight sequence**, because the model needs both core assembly and at least one technology (or utility) to produce a meaningful balance.

- **Energy balance (Slices 5, 6, 7):** The electrical balance is `sum(supply_terms) == electricity_load + sum(load_terms)`. For this to be **verifiable** (and feasible without trivial zeros), there must be at least one **supply** term. So:
  - **Slice 5** (core skeleton) can be done with **mock** supply/load terms so the model compiles and solves (e.g. a fixed dummy supply expression).
  - **Slice 6** (islanded balance baseline) is about wiring real load data and checking residuals. That check is only meaningful if **at least one real supply technology** exists—otherwise there is nothing to balance the load. So **Slice 6 and Slice 7 (Solar PV) are effectively built in parallel**: implement the core balance and registration (5), add the Solar PV block (7) so it registers `solar_generation` as a supply term, then complete the islanded baseline (6) by wiring real load and verifying the balance with real PV supply.
- **Suggested minimum for “balance works”:** Core (5) + Solar PV (7) + islanded baseline steps (6). Optionally add unserved-energy slack in (5) or (6) so that infeasible cases still solve with slack.

Other couplings:

- **Slice 4b (solar loader)** should be done before or with **Slice 7** so the DataContainer has `solar_production_keys` and series for the PV block to read.
- **Slice 10 (hydrogen)** needs **core** to add a hydrogen balance constraint; the three H2 sub-blocks (electrolyzer, storage, fuel cell) can be built in one slice but together they form one subsystem.
- **Slice 14 (network)** needs multiple technologies and load buses so there is something to map to nodes; it builds on top of 5–7 (and typically 8, 11, etc.).

Once the energy balance works with solar (and optionally storage and one utility, e.g. import/export), **everything returns to serial addition**: each subsequent technology or utility (hydrokinetic, diesel, hydrogen, remaining hydro, network) is added one at a time by implementing the block and registering terms with core—no further parallel coupling needed.

When planning sprints or PRs, treat **5 + 7 + 6** as one logical unit for “first working islanded electrical balance with one supply technology.”

---

## Execution tracker (status, owner, evidence)

Use this table as the current implementation status board. Update whenever a slice gate changes state.

Status legend: `complete`, `mostly_complete`, `partial`, `not_started`.

| Slice | Status | Owner | Evidence |
|---|---|---|---|
| 0 | complete | @team | `requirements/deropt_rebuild_spec.md`, `.gitignore` |
| 1 | complete | @team | package scaffold present; `tests/test_scaffold_imports.py` |
| 2 | partial | @team | `config/case_config.py`, `config/cases/*` (case selection works; tech toggles/solver options still pending) |
| 3 | mostly_complete | @team | `data_loading/schemas.py` (`DataContainer` + minimum field validation) |
| 4a | mostly_complete | @team | `data_loading/loaders/energy_load.py`, `tests/test_load_energy_csv.py` |
| 4b | mostly_complete | @team | `data_loading/loaders/resource_profiles.py`, `tests/test_resource_profiles.py` |
| 4c | partial | @team | `run/playground.py` orchestrates load + optional solar; optional loaders/rate alignment pending |
| 5 | not_started | @team | `model/core.py` currently returns placeholder model |
| 6 | not_started | @team | no islanded balance constraints yet |
| 7 | not_started | @team | `technologies/solar_pv.py` currently stub |
| 8 | not_started | @team | `technologies/battery_energy_storage.py` currently stub |
| 9 | not_started | @team | `technologies/hydrokinetic.py` currently stub |
| 10 | not_started | @team | hydrogen modules currently stubs |
| 11 | not_started | @team | `technologies/diesel_generation.py` currently stub |
| 12 | partial | @team | `run/playground.py` exists; full one-command solve/regression artifacts pending |
| 13 | not_started | @team | hydro family modules currently stubs |
| 14 | not_started | @team | `utilities/network.py` currently stub |

---

## Sub-task tracker (step-level status, owner, evidence)

This is the step-level companion to the slice tracker above. Keep step IDs aligned to the numbered rows in each slice table.

| Step | Status | Owner | Evidence |
|---|---|---|---|
| 0.1 | complete | @team | `requirements/deropt_rebuild_spec.md` exists and references canonical plan |
| 0.2 | complete | @team | `.gitignore` excludes `data/`, temp/debug outputs, and pytest artifacts |
| 0.3 | complete | @team | data path configuration documented in `README.md` and `config/cases/*` |
| 1.1 | complete | @team | `config/`, `data_loading/`, `model/`, `utilities/`, `technologies/`, `run/`, `tests/` exist |
| 1.2 | complete | @team | package `__init__.py` files present and importable |
| 1.3 | complete | @team | placeholder modules present (`model/core.py`, `technologies/*.py`, `utilities/*.py`) |
| 1.4 | complete | @team | `tests/test_scaffold_imports.py` imports scaffold modules successfully |
| 2.1 | partial | @team | `config/case_config.py` has case/data schema; solver options not yet modeled |
| 2.2 | not_started | @team | no technology enable flags or utility/network toggles in `CaseConfig` yet |
| 2.3 | mostly_complete | @team | `discover_load_file`, `discover_solar_file` implemented (wind/hydro discovery not yet) |
| 2.4 | mostly_complete | @team | case modules in `config/cases/*`; load path validation currently occurs at loader runtime |
| 3.1 | complete | @team | `DataContainer` defined in `data_loading/schemas.py` |
| 3.2 | complete | @team | required keys validated (`indices.time`, `timeseries.time_serial`, `static.electricity_load_keys`) |
| 3.3 | complete | @team | `validate_minimum_fields()` called in `load_energy_load()` |
| 3.4 | partial | @team | optional keys partially implied by code/docs; no dedicated contract doc/module yet |
| 4a.1 | complete | @team | CSV/XLSX/XLS loaders + header dedup in `energy_load.py` |
| 4a.2 | complete | @team | configured load column with fallback `(kW)/(kWh)` detection implemented |
| 4a.3 | complete | @team | datetime parsing supports text, Excel serial, MATLAB serial, auto-detect |
| 4a.4 | complete | @team | unit inference + kW to kWh conversion using inferred `dt_hours` |
| 4a.5 | complete | @team | conditioning includes optional resample + NaN/negative interpolation |
| 4a.6 | complete | @team | per-column `electricity_load__{suffix}` keys + static metadata emitted |
| 4a.7 | complete | @team | returns aligned `DataContainer`; validated before return |
| 4b.1 | mostly_complete | @team | tests include synthetic/demo solar fixtures in `tests/test_resource_profiles.py` |
| 4b.2 | complete | @team | solar CSV loader with time-column detect or row-count interval inference |
| 4b.3 | complete | @team | negative filtering + interpolation implemented and tested |
| 4b.4 | complete | @team | time-of-year alignment to load `datetime` implemented |
| 4b.5 | complete | @team | conversion to `kWh/kW` via `time_step_hours` implemented |
| 4b.6 | complete | @team | `solar_production__*` keys + static metadata written to container |
| 4c.1 | complete | @team | load-derived master time vector used by solar alignment path |
| 4c.2 | not_started | @team | optional wind/hydro resource loaders not yet implemented |
| 4c.3 | not_started | @team | optional import/export rate vector alignment not yet implemented |
| 4c.4 | partial | @team | unit-level regression tests exist; baseline-case contract test still pending |
| 5.1-5.6 | not_started | @team | `model/core.py` still placeholder |
| 6.1-6.4 | not_started | @team | islanded balance baseline not implemented |
| 7.1-7.5 | not_started | @team | `technologies/solar_pv.py` stub |
| 8.1-8.5 | not_started | @team | `technologies/battery_energy_storage.py` stub |
| 9.1-9.5 | not_started | @team | `technologies/hydrokinetic.py` stub |
| 10.1-10.4 | not_started | @team | hydrogen subsystem modules + core H2 balance not implemented |
| 11.1-11.5 | not_started | @team | `technologies/diesel_generation.py` stub |
| 12.1-12.4 | partial | @team | `run/playground.py` exists; solve+artifacts+full harness pending |
| 13.1-13.4 | not_started | @team | remaining hydro modules are stubs |
| 14.1-14.6 | not_started | @team | `utilities/network.py` stub |

---

## Gate metrics (numeric acceptance defaults)

Use these numeric criteria in addition to functional checks. If a case needs different tolerances, document the override in the PR.

| Slice | Numeric gate criteria |
|---|---|
| 3 | Required-field validation errors are deterministic and include missing key names; all schema tests pass (`100%` pass in `tests/test_*schema*` and loader-contract tests). |
| 4a | Load length equals datetime length exactly (`len(load_series) == len(datetime)` for all load keys); no NaN values after conditioning; converted units are kWh for all load keys. |
| 4b | Solar series length matches load length exactly for all solar keys; no negative and no NaN output values; output units are `kWh/kW`. |
| 4c | End-to-end data build for baseline case completes with zero manual file edits; `indices["time"]` is strictly increasing; all aligned series lengths equal `|T|`. |
| 5 | Core model builds with no unresolved component references; smoke solve terminates with feasible/optimal status on a tiny fixture case in under 30 seconds. |
| 6 | Electrical balance residual max absolute error <= `1e-6` (in model energy units) across all `t`; if slack enabled, total slack energy <= `1e-4` of annual load for validation case. |
| 7 | PV-only baseline solve feasible; `solar_generation[t] <= solar_capacity_total * solar_production[t] + 1e-9` for all `t`; adopted capacity non-negative. |
| 8 | SOC recursion residual <= `1e-6` across all `t`; SOC bounds respected within `1e-8`; no simultaneous charge/discharge if exclusivity is enabled. |
| 9 | Hydrokinetic generation bound residual <= `1e-6`; annual generation within agreed validation band for benchmark dataset. |
| 10 | Hydrogen balance residual max absolute error <= `1e-6`; all H2 SOC/state bounds satisfied within `1e-8`. |
| 11 | MILP commitment logic has zero violated indicator/link constraints at tolerance `1e-6`; objective decomposition reproduces expected fuel/startup behavior on fixture case. |
| 12 | Regression suite passes `100%`; baseline result artifact hash/signature is stable across reruns for fixed solver/version/settings. |
| 13 | Hydro modules hit target validation bands where data exists (default: annual and peak generation each within +/-5%). |
| 14 | Nodal balance residual <= `1e-6`; branch/transformer limits satisfied within `1e-6`; post-opt ACPF validation has no voltage/ampacity violations for validation case. |

---

## Slice 0 — Plan handoff + repo hygiene

| # | Step | Notes |
|---|------|--------|
| 0.1 | Create/update `requirements/deropt_rebuild_spec.md` with plan summary and scope | Canonical requirements anchor |
| 0.2 | Add/update `.gitignore`: exclude `data/`, run artifacts, `temp_*`, `.pytest_cache` | Keep code/config tracked; data and outputs untracked |
| 0.3 | Document where data paths are configured (config or env) | So collaborators know how to point at `data/` |

**Gate:** Requirements file in repo; `.gitignore` correctly excludes data and artifacts.

---

## Slice 1 — Scaffold and package layout

| # | Step | Notes |
|---|------|--------|
| 1.1 | Create directories: `config/`, `data_loading/`, `data_loading/loaders/`, `model/`, `utilities/`, `technologies/`, `run/`, `tests/` | Layout per rebuild plan |
| 1.2 | Add minimal `__init__.py` (or stubs) so each package is importable | No circular imports |
| 1.3 | Add placeholder modules where needed (e.g. `model/core.py`, `technologies/solar_pv.py`) so imports resolve | Stubs only; no logic |
| 1.4 | Add a single scaffold test that imports main packages and passes | Test runner discovers tests |

**Gate:** Project imports cleanly; test runner discovers and runs scaffold test.

---

## Slice 2 — Case config and scenario toggles

| # | Step | Notes |
|---|------|--------|
| 2.1 | Define config schema (dataclass or YAML): case name, data paths (load, solar, etc.), solver options | Single source of truth for a run |
| 2.2 | Add technology enable flags (e.g. `solar_pv_enabled`, `battery_enabled`) and utility/network flags | No code changes to toggle techs |
| 2.3 | Add file path/pattern fields (load file, solar file, optional wind/hydro) and discovery helpers | `discover_load_file`, `discover_solar_file` style |
| 2.4 | Wire one case config (e.g. igiugig) to load from a case folder; validate paths exist or fail fast | Gate: one config switches PV on/off and paths |

**Gate:** One config file can switch PV (and other toggles) and paths without code edits.

---

## Slice 3 — Data contract (DataContainer + validation)

| # | Step | Notes |
|---|------|--------|
| 3.1 | Define `DataContainer`: `indices`, `timeseries`, `static`, `tech_params` | Typed or documented structure |
| 3.2 | Define required keys: e.g. `indices["time"]`, `timeseries["time_serial"]`, `static["electricity_load_keys"]` (non-empty), and that each key in that list exists in `timeseries` | Schema for “minimum viable” container |
| 3.3 | Implement `validate_minimum_fields()` (or equivalent) and call it after loaders build the container | Invalid/missing data fails fast with clear errors |
| 3.4 | Document optional keys (solar, wind, rates, network) so later slices know what to expect | No need to implement all yet |

**Gate:** Invalid/missing required data fails fast; valid case builds a typed container successfully.

---

## Slice 4 — Data loading and time alignment pipeline

### 4a. Electricity load loader (sub-portion)

| # | Step | Notes |
|---|------|--------|
| 4a.1 | Support CSV and Excel: read headers, deduplicate column names, resolve datetime column | `_load_rows_from_csv` / `_load_rows_from_excel`, `_deduplicate_headers` |
| 4a.2 | Resolve load columns: configured column or fallback to columns with `(kW)`/`(kWh)` in header | `_resolve_load_columns` |
| 4a.3 | Parse datetimes (text, Excel serial, MATLAB serial, or auto-detect) and sort by time | `_parse_datetime_cell`, sort rows |
| 4a.4 | Infer units from headers; convert kW → kWh using time step (energy = power × dt_hours) | `_infer_units_from_header`; apply conversion |
| 4a.5 | Condition time series: optional resample to target interval (only if irregular), fill NaN/negative via interpolation | `_condition_time_series` |
| 4a.6 | Build timeseries: one key per load column `electricity_load__{suffix}`; set `electricity_load_keys`, `load_columns`, `load_units` = "kWh", `time_step_hours` | Single representation; no duplicate “first column” key |
| 4a.7 | Return `DataContainer` with `indices["time"]`, `timeseries` (datetime, time_serial, electricity_load__*), `static` (time_step_hours, load_units, electricity_load_keys, load_columns) | |

### 4b. Solar resource loader (sub-portion)

| # | Step | Notes |
|---|------|--------|
| 4b.1 | Set up / support demo solar file: e.g. one or more columns (fixed, 1D tracking), capacity factor 0–1 or similar | Test fixtures or example data |
| 4b.2 | Load solar CSV: detect time column or infer interval from row count (8760 → hourly, 35040 → 15-min); read numeric columns only | `load_solar_into_container` |
| 4b.3 | Filter data: treat negatives as missing; fill NaN (and any gaps) via interpolation | `treat_negative_as_missing`, interpolate |
| 4b.4 | Fit to load: align by time-of-year (minutes from start of year) to `data.timeseries["datetime"]`; reindex/interpolate so solar length = load length | Same T as load |
| 4b.5 | Convert to kWh/kW: multiply each value by `data.static["time_step_hours"]` (CF × dt_hours) | Output units: kWh per kW capacity |
| 4b.6 | Write to container: `solar_production__{suffix}` per column; `static["solar_production_keys"]`, `solar_production_units` = "kWh/kW", `solar_production_columns` | |

### 4c. Pipeline orchestration

| # | Step | Notes |
|---|------|--------|
| 4c.1 | Establish master time index from load (after load conditioning); all other loaders use this length/datetimes | Time vector = load’s time |
| 4c.2 | Optional: wind, hydro resource loaders (same pattern: load → filter → align to load time → units) | Can defer to later slices |
| 4c.3 | Optional: load import/export rate vectors and align to time index | Slice 6 or utility slice |
| 4c.4 | Add minimal regression harness now (moved earlier from Slice 12): tests for container key contract and load/solar alignment lengths on baseline case | Prevent regressions before model-core work starts |

**Gate:** Loader can ingest a case folder and produce a complete, aligned DataContainer (load + optional solar) without manual file-by-file edits.

---

## Slice 5 — Core model skeleton + registration interfaces

| # | Step | Notes |
|---|------|--------|
| 5.1 | Create Pyomo `ConcreteModel`; define Sets from DataContainer (e.g. `T` from `indices["time"]`, optional `K` for load buses) | core.py |
| 5.2 | Define Params from DataContainer: e.g. electricity load from `electricity_load_keys` and timeseries; time_step if needed | No tech-specific params in core |
| 5.3 | Define registration interface: core collects “supply terms” and “load terms” for electrical (and later thermal, H2) and “cost expressions” from blocks | e.g. lists or callbacks that blocks append to |
| 5.4 | Add electrical balance constraint: `sum(supply_terms) == sum(load_terms)` using registered terms only (no hard-coded tech names) | Placeholder supply/load if needed |
| 5.5 | Add objective: `minimize(sum(registered_cost_expressions))`; no formula logic in core | Mock terms so objective compiles |
| 5.6 | Enforce variable bounds (e.g. non-negative where appropriate) | Mirror MATLAB `lb(:)=0` intent |

**Gate:** With mock terms (or with one real technology block, e.g. Solar PV from Slice 7), balances and objective compile and model solves in a smoke test. See **Dependencies and parallel work**: a meaningful balance requires at least one supply technology, so 5 is often completed in the same pass as 7.

---

## Slice 6 — Islanded electrical balance baseline

**Depends on:** Slice 5 (core) and **at least one supply technology** (e.g. Slice 7 Solar PV). Without any technology block registering supply terms, there is nothing to satisfy the load—balance verification is only meaningful once e.g. PV is present. Build 6 in parallel with or immediately after 7.

| # | Step | Notes |
|---|------|--------|
| 6.1 | Ensure electrical balance uses only registered supply/load terms; no utility import/export in baseline | Islanded = no grid |
| 6.2 | Add optional feasibility slack (e.g. `unserved_electricity[t] >= 0`) with large penalty so balance remains equality | Slack only when unavoidable |
| 6.3 | Wire real load data from DataContainer into core Params (electricity load per bus if multi-node, else single vector) | |
| 6.4 | Solve with at least one supply tech (e.g. Solar PV) enabled; verify balance residuals are clean and solution is feasible for a prior-study style input | |

**Gate:** Baseline feasibility and clean balance residuals on islanded case **with at least one technology** (e.g. PV) supplying the balance.

---

## Slice 7 — Solar PV technology block

**Used by:** Slice 6 (islanded balance) needs at least one supply technology; Solar PV is the first and is the natural partner so that “balance works” can be verified. Slice 4b (solar loader) should be in place so `solar_production_keys` and timeseries exist in the DataContainer.

| # | Step | Notes |
|---|------|--------|
| 7.1 | **Data → Pyomo:** Read `solar_production_keys` and corresponding timeseries from DataContainer; create Pyomo Param(s) for solar production (kWh/kW) per t (and per column if multiple) | e.g. `solar_production[t]` or indexed by profile |
| 7.2 | **Technical and economic parameters:** Add params for capital cost (e.g. $/kW), O&M ($/kW-yr or similar), optional efficiency/degradation, max capacity limit; optional `existing_solar_capacity` (and existing cost terms) from tech_params | From config or tech_params |
| 7.3 | **Decision variables:** Define `solar_capacity_adopted` (or `photovoltaic_capacity_adopted`), `solar_generation[t]` (or per profile); all >= 0 | |
| 7.4 | **Solar-specific constraints:** (1) Production limited by installed capacity and resource: `solar_generation[t] <= (existing_solar_capacity + solar_capacity_adopted) * solar_production[t]` (or sum over profiles if multiple); (2) Non-negativity of capacity and generation | |
| 7.5 | **Shared constraint interface:** Register `solar_generation` as **electricity_supply_term**; register **objective contribution** (capital annuity + O&M for adopted capacity, optional O&M for existing) | Core sums these in balance and objective |

**Gate:** PV-only or PV + baseline run solves; output traces (capacity, generation) are reasonable.

---

## Slice 8 — Battery storage technology block

| # | Step | Notes |
|---|------|--------|
| 8.1 | **Data → Pyomo:** No resource profile needed for battery; use `time_step_hours` from DataContainer for SOC dynamics | |
| 8.2 | **Technical and economic parameters:** Energy capacity (kWh) and power (kW) capital and O&M; round-trip efficiency; min/max SOC (e.g. 0–100% or 10–90%); max charge/discharge rate (fraction of capacity or absolute); optional `existing_battery_capacity` | |
| 8.3 | **Decision variables:** `battery_capacity_adopted` (energy), optional power capacity; `battery_charge[t]`, `battery_discharge[t]`, `battery_state_of_charge[t]`; all >= 0 | |
| 8.4 | **Battery-specific constraints:** (1) SOC dynamics: `soc[t] = soc[t-1] + charge[t]*eta_c*dt - discharge[t]/eta_d*dt` (or equivalent); (2) SOC bounds; (3) Charge/discharge rate limits; (4) Non-negativity | |
| 8.5 | **Shared constraint interface:** Register `battery_discharge` as electricity_supply; `battery_charge` as electricity_load; objective = capital annuity + O&M | |

**Gate:** SOC trajectories and charge/discharge behavior match expected physics and prior trend.

---

## Slice 9 — Hydrokinetic technology block

| # | Step | Notes |
|---|------|--------|
| 9.1 | **Data → Pyomo:** Load hydrokinetic resource profile (available power or capacity factor) aligned to load time; create Param for resource per t | Similar to solar; may be single profile |
| 9.2 | **Technical and economic parameters:** Capital and O&M; efficiency; max capacity; optional existing capacity | |
| 9.3 | **Decision variables:** `hydrokinetic_capacity_adopted`, `hydrokinetic_generation[t]` >= 0 | |
| 9.4 | **Hydrokinetic-specific constraints:** Generation <= resource[t] and/or <= capacity * resource_factor[t]; non-negativity | |
| 9.5 | **Shared constraint interface:** Register generation as electricity_supply; objective contribution | |

**Gate:** Hydrokinetic subset reproduces prior-study behavior; realistic resource-to-power.

---

## Slice 10 — Hydrogen LP subsystem (PEM electrolyzer, H2 storage, PEM fuel cell)

| # | Step | Notes |
|---|------|--------|
| 10.1 | **PEM electrolyzer:** Data (none beyond time step); params (efficiency, capital, O&M, existing capacity); vars (capacity_adopted, h2_production[t]); constraint: h2_production = f(electricity_in); register electricity_load_term and H2 supply term; objective term | |
| 10.2 | **Compressed gas H2 storage:** Params (capacity, efficiency, SOC bounds); vars (charge, discharge, soc[t]); SOC dynamics; register H2 charge as H2 load, discharge as H2 supply; objective term | |
| 10.3 | **PEM fuel cell:** Params (efficiency, capital, O&M); vars (capacity_adopted, electricity[t], h2_consumption[t]); constraint: electricity_out = f(h2_in); register electricity_supply and H2_load; objective term | |
| 10.4 | **Core:** Add hydrogen balance constraint: sum(H2 supply) == sum(H2 load) + external H2 demand (if any) | |

**Gate:** H2 balance closes; cost/dispatch directionally consistent with prior runs.

---

## Slice 11 — Diesel MILP block

| # | Step | Notes |
|---|------|--------|
| 11.1 | **Data → Pyomo:** Time step; optional fuel price or cost params | |
| 11.2 | **Parameters:** Capital, O&M, heat rate / efficiency, min/max load, startup/shutdown costs if needed; existing capacity | |
| 11.3 | **Decision variables:** `diesel_capacity_adopted`, `diesel_generation[t]`; binaries: commitment (on/off) per t, optional startup/shutdown | |
| 11.4 | **Diesel-specific constraints:** Generation <= capacity * commitment; min load when on; max load; fuel = f(generation); linking constraints for startup/shutdown | |
| 11.5 | **Shared constraint interface:** Register generation as electricity_supply; objective (capital + O&M + fuel + startup/shutdown) | |

**Gate:** Commitment states and objective behavior align with prior MILP baseline.

---

## Slice 12 — Playground runner + regression harness

| # | Step | Notes |
|---|------|--------|
| 12.1 | Finalize `run/playground.py`: case setup → load config → data pipeline (load + solar + optional others) → build model (core + enabled techs) → solve → extract results | One-command run |
| 12.2 | Expand regression checks from the early minimal harness (added in Slice 4c) to full slice-by-slice checks (e.g. slice 7 PV vars/constraints, slice 8 SOC recursion, objective term registry integrity) | Required for ongoing integration safety |
| 12.3 | Produce reproducible report artifacts (capacities, dispatch, costs) and optionally CSV/plots | |
| 12.4 | Document baseline case and DERopt parity comparison notes (v0.x functional test package) | BP1 traceability |

**Gate:** One-command rerun of baseline case with reproducible artifacts; v0.x test package and parity notes.

---

## Slice 13 — Remaining hydro family (run-of-river, dam, pumped)

| # | Step | Notes |
|---|------|--------|
| 13.1 | **Run-of-river:** Resource profile (flow/head or available power); capacity + generation vars; generation <= resource; register supply + objective | |
| 13.2 | **Dam hydro:** Inflow profile, reservoir state vars, storage continuity, min/max storage, turbine bounds; capacity + generation; register supply + objective | |
| 13.3 | **Pumped hydro:** Charge/discharge/SOC (or reservoir volume); round-trip efficiency; optional exclusivity (MILP); register discharge as supply, charge as load; objective | |
| 13.4 | Validation: Where data exists, target +/-5% annual generation and +/-5% peak generation vs prior study | |

**Gate:** Each hydro module reproduces prior-study behavior; validation error bands where applicable.

---

## Slice 14 — Network detail and multi-node expansion

| # | Step | Notes |
|---|------|--------|
| 14.1 | **Data:** Load T_map (load_bus → node), branch params (from MATPOWER or table), transformer ratings | |
| 14.2 | **Sets and params:** Nodes N, branches B; admittance or branch data from MATPOWER | |
| 14.3 | **Variables:** Pinj[n,t], Qinj[n,t]; optionally angles, voltages, flows for DLPF | |
| 14.4 | **Constraints:** Power injection = net gen - net load per node (using T_map); transformer polygon C*[P;Q] <= rating; optional DLPF equations and voltage limits | |
| 14.5 | **Integration:** Net generation and load per node aggregated from technology and load data; core or network block sums to Pinj/Qinj | |
| 14.6 | Post-optimization: AC power flow validation (voltage, ampacity) as separate step, not in-loop | |

**Gate:** Nodal balance and flow/voltage behavior match prior multi-node scenarios; ACPF validation for compliance.

---

## Summary table (slices → step counts)

| Slice | Scope | # concrete step groups | Notes |
|-------|--------|-------------------------|--------|
| 0 | Plan handoff + repo hygiene | 3 | |
| 1 | Scaffold and package layout | 4 | |
| 2 | Case config and scenario toggles | 4 | |
| 3 | Data contract (DataContainer + validation) | 4 | |
| 4 | Data loading and time alignment | 4a: 7, 4b: 6, 4c: 3 | 4b feeds Slice 7 (solar data) |
| 5 | Core model skeleton + registration | 6 | Build with 7 for first real balance |
| 6 | Islanded electrical balance baseline | 4 | **Requires** at least one supply tech (e.g. 7) |
| 7 | Solar PV technology block | 5 | First supply tech; pair with 5+6 |
| 8 | Battery storage technology block | 5 | Adds load + supply terms |
| 9 | Hydrokinetic technology block | 5 | |
| 10 | Hydrogen LP subsystem | 4 | Core must add H2 balance |
| 11 | Diesel MILP block | 5 | |
| 12 | Playground runner + regression | 4 | |
| 13 | Remaining hydro family | 4 | |
| 14 | Network and multi-node | 6 | Builds on multiple techs + load buses |

**Parallel / coupled:** For a first "energy balance works" outcome, plan **Slices 5, 7, and 6** together (core + Solar PV + islanded baseline). See **Dependencies and parallel work** above. Once that baseline is in place (and optionally storage + one utility), **everything else is serial addition**—each new technology or utility is added one at a time; no further parallel coupling is required.

This list can be refined further (e.g. split 4b into “single column” vs “multiple columns” steps, or add wind loader steps under 4c). Use it as the initial checklist and adjust per sprint or PR scope.
