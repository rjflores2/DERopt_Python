# DERopt Plan

This is the single planning document for the Python/Pyomo rebuild. `README.md` is the user/developer operating guide; this file is the roadmap and architecture reference.

## Purpose

- Rebuild DERopt in Python using Pyomo with a modular architecture.
- Keep core model assembly generic; put technology and utility logic in separate modules.
- Support staged delivery (slice-based) with testable milestones.

## Architecture

Repository structure and ownership:

- `config/`: case configs and discovery
- `data_loading/`: file loaders and data contract (`DataContainer`)
- `model/core.py`: model assembly, balances, and objective aggregation
- `technologies/`: one module per technology (solar, storage, diesel, etc.)
- `utilities/`: tariffs/import-export/network modules
- `run/playground.py`: local orchestration entry point
- `tests/`: unit/regression tests

Design rules:

- Core is the meeting place, not the business-logic owner.
- Technologies/utilities register balance and objective contributions.
- Data loaders normalize units/time alignment before model build.
- Fail fast on invalid config/data/dispatch conditions (no silent fallback).

## Implementation Slices

Use this ordered roadmap for execution:

1. Scaffold and package hygiene
2. Case config/discovery
3. Data contract and minimum validation
4. Data loading pipeline (load/resource/rates alignment)
5. Core model assembly and registration interfaces
6. Islanded electrical balance baseline
7. Solar PV block
8. Battery block
9. Hydrokinetic block
10. Hydrogen subsystem
11. Diesel MILP block
12. Runner + regression harness
13. Remaining hydro family
14. Network/multi-node expansion

## Current Snapshot

As of this consolidation:

- Implemented and active:
  - Load loader (`energy_load`) with CSV/XLSX/XLS support and conditioning
  - Solar resource loader (`resource_profiles`) aligned to load time vector
  - Core model assembly (`model/core.py`) with node/time balance hooks
  - Solar PV technology block with objective/source integration
  - Utility-rate loader framework with initial SCE parser
- Partial/in-progress:
  - Utility rate robustness (shape handling, strict validation, tests)
  - Additional technologies beyond solar
  - Solve/report pipeline completion in `run/playground.py`

## Quality Gates

Use these minimum gates before marking a slice complete:

- Loader outputs are length-aligned to `|T|` and unit-normalized
- No silent exception swallowing in critical orchestration paths
- Contract tests cover malformed inputs and expected happy paths
- Root `pytest` run is deterministic (`pytest.ini`-driven)
- New module additions require fixture-based tests

## Extension Workflow

When adding a technology or utility:

1. Add module file in `technologies/` or `utilities/`.
2. Define inputs/parameters via config and validated data contract.
3. Register balance and objective terms with core.
4. Add targeted tests (unit + at least one integration/fixture test).
5. Update this plan snapshot if milestone/state changed.

## Decision Log (Active)

- Keep docs minimal: one `README.md` + one planning document (`docs/PLAN.md`).
- Prefer fail-fast behavior over silent recovery in discovery/dispatch code.
- Keep plugin-like dispatch for utility rates; enforce strict normalized output contracts.
