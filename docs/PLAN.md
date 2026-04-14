# DERopt Plan

Single planning document for the Python/Pyomo rebuild. **`README.md`** is the operating guide for users; **`docs/DEVELOPMENT.md`** is the day-to-day contributor map. This file is the roadmap, architecture reference, and milestone tracker.

## Purpose

- Rebuild DERopt in Python using **Pyomo**, solved with **Gurobi**.
- Keep **`model/core.py`** generic: time and nodes, electricity/hydrogen balances, objective aggregation, and registration of blocks.
- Put **technology** and **utility** logic in dedicated packages with a small, repeatable contract (balance terms, objective terms, optional diagnostics).
- Support **staged delivery** with testable milestones and a **fail-fast** data/config contract.

## Architecture

Repository structure and ownership:

| Area | Role |
|------|------|
| `config/` | `CaseConfig`, financials, time subset, case discovery (`config/cases/*.py`) |
| `data_loading/` | Loaders and **`DataContainer`**; unit/time alignment before model build |
| `model/core.py` | Sets `T`, `NODES`, technology loop, utility registration, balance, objective |
| `technologies/` | Opt-in blocks via **`REGISTRY`** in `technologies/__init__.py` |
| `utilities/electricity_import_export/` | Grid import, energy cost, demand charges, fixed customer charges (not in `REGISTRY`) |
| `run/playground.py` | Local orchestration; `run/build_run_data.py` assembles `DataContainer` |
| `utilities/results.py` | Solution extraction and optional CSV export |
| `tests/` | Unit and integration tests |

Design rules:

- Core is the **meeting place**, not the owner of technology or tariff algebra.
- Technologies and the utility block expose balance terms (e.g. **`electricity_source_term` / `electricity_sink_term`**, **`hydrogen_source_term` / `hydrogen_sink_term`**) plus **`objective_contribution`** (and related reporting expressions) so core stays declarative.
- Loaders **normalize units** and keep per-timestep series **length-aligned** after optional `time_subset`.
- **Fail fast** on invalid config, missing files, bad lengths, or inconsistent keys (no silent fallback).

## Implementation status (living roadmap)

### Done (baseline product)

1. Package layout, case discovery, `pytest` + `pyproject.toml`.
2. **`DataContainer`** with `validate_minimum_fields()`.
3. Electricity **load** loader (CSV/XLSX/XLS), conditioning, multi-node keys (`electricity_load__*`).
4. **Solar** resource loader aligned to load timestamps (time-of-year mapping); multi-profile keys (`solar_production__*`).
5. **Hydrokinetic** resource loader and technology block (multi-profile keys, formulations aligned with other DER registry patterns).
6. **Utility** pipeline: OpenEI router + utility-specific parsers (e.g. SCE), **TOU** import prices from schedules, **raw** CSV price override, **`utility_tariffs`** + **`node_utility_tariff`** for per-meter tariffs.
7. **Utility block**: energy imports, **flat / TOU / combined** demand charges, **fixed customer charges** (horizon USD); attaches when any of those apply.
8. **Core** model: per-node electricity and hydrogen balances, optimizing + non-optimizing cost reporting; registry technologies validated on attach (`model/contracts.py`).
9. **Solar PV** block: per-node, per-profile capacity and generation; area limits; existing PV; optional capital recovery on existing.
10. **Battery** and **flow-battery** blocks: SOC / charge / discharge, C-rates, adoption.
11. **Diesel generator** block: multiple formulations (`diesel_lp`, `diesel_binary`, `diesel_unit_milp`), fuel economics, per-node limits.
12. **Playground**: load → build → Gurobi solve → summary + optional diagnostics and CSV export.
13. Example cases including **`max capability`** (stresses multi-node, multi-solar, battery, diesel, multi-tariff, financing); hydrogen technologies are in the registry for case builders but are not required in shipped examples.
14. **Hydrogen subsystem**: PEM electrolyzer, alkaline electrolyzer, PEM fuel cell, and compressed-gas H2 storage with canonical **kWh-H2_LHV** basis and core hydrogen balance; electrolyzer/fuel-cell **`formulation`** strings follow the diesel **`technology_model`** pattern (e.g. `pem_electrolyzer_lp`).

### In flight / tighten next

- Broader **OpenEI utility** coverage and stricter validation of parsed shapes.
- **Regression / benchmark** cases pinned to small horizons for CI performance.
- **Diagnostics** coverage (tariffs, equipment costs) consistently surfaced in runs.

### Next major slices (priority order — adjust as product needs change)

1. **Additional renewables** — e.g. wind: loader + `DataContainer` keys + technology block (same registration pattern as solar / hydrokinetic).
2. **Export / sellback** — export energy variable and tariff-aware revenue (or credits) where data supports it.
3. **Energy rate types** beyond TOU in the utility block — e.g. monthly/daily tiered energy already partially modeled in `ParsedRate`; wire full cost logic if not complete.
4. **Hydrogen — next modeling** — exogenous hydrogen demand, additional storage forms, reporting conversions (e.g. kg-H2 via explicit LHV conversion).
5. **Physical network** — beyond “one meter per load column”: lines, losses, multi-bus OPF-style or transport approximations.

## Quality gates

Before treating a slice as complete:

- Loader outputs are **length-aligned** to `|T|` and **unit-normalized** as documented.
- No silent exception swallowing on orchestration paths.
- **Contract tests** for malformed inputs and at least one happy path per new loader or block.
- **`pytest`** from repo root is deterministic (`pyproject.toml` / `pythonpath` for tests).
- New technology or utility surface area includes **targeted tests** (unit + integration where feasible).

## Extension workflow

When adding a technology or utility capability:

1. Add or extend modules under `technologies/` or `data_loading/loaders/utility_rates/`.
2. Define **defaults and validation** in an `inputs.py` (or loader-local validation) and merge from `technology_parameters` / case config.
3. Register: **technology** → tuple in `technologies.REGISTRY`; **utility** → `@register_utility` parser returning **`ParsedRate`**.
4. Ensure **`build_run_data`** populates everything the block needs on `DataContainer` (or `model` attachments from canonicalized data).
5. Add tests; update **`README.md`** if user-facing behavior changes; update **this plan** and **`DEVELOPMENT.md`** if the architecture or workflow shifts.

## Decision log (active)

- **Docs set:** `README.md` (usage + data + config), `docs/DEVELOPMENT.md` (contributor map), `docs/PLAN.md` (this roadmap).
- **Fail fast** over silent recovery in loaders, config resolution, and model build.
- **Plugin-style** utility parsers with a strict **`ParsedRate`** contract; core and the utility block do not branch on utility names.
- **Technologies are opt-in** via `technology_parameters`: absent key or **`None`** skips; **`{}` or a dict** requests a build and **must** result in an attached `model.<name>` block or `build_model` raises (no silent skip for resource-dependent tech when requested).
