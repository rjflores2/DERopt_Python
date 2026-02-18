# DERopt Rebuild Requirements Spec

This requirements spec captures the agreed implementation baseline for the DERopt MATLAB-to-Python/Pyomo rebuild.

## Canonical Plan

The full detailed plan is maintained in:

- `docs/deropt_python_pyomo_rebuild.md`

This spec file exists to satisfy Slice 0 handoff requirements and to provide a stable requirements anchor for collaborators.

## Scope Baseline

- Architecture is modular: `data_loading/`, `model/core.py`, `utilities/`, `technologies/`, `run/playground.py`.
- Technology modules own parameters, constraints, and objective terms for that technology.
- Core assembles balances/objective from registered module contributions; it does not hard-code per-tech/per-utility logic.
- Time handling uses index-based equations plus a canonical `time_serial[t]` vector in timeseries data.
- Demand charges are out-of-scope for baseline slices unless case-required; if enabled, they must follow utility tariff definitions exactly.
- Network optimization uses tractable in-loop constraints; AC power flow tooling is for post-optimization validation.

## DOE SOPO Alignment (DE-EE0011656.0001)

- **Platform requirement**: HydroFlex is implemented in Python with Pyomo using an object/module-oriented structure on an open-source repository.
- **Core objective**: minimize total cost including amortized capital, fixed/variable O&M, fuel costs, minus applicable energy/market revenues.
- **Minimum model content**:
  - time-series conditioning with common timestamps/units and anomaly filtering,
  - hydro, solar, wind, storage, and existing fossil technologies with technoeconomic characterization,
  - common constraints and cost function that auto-adjust based on enabled technologies.
- **Hydro scope (BP1/BP2)**: support hydrokinetic, run-of-river, and reservoir-style models (with marine/wave where case-applicable), then validate and iterate with case-study data.
- **Network scope**: linearized in-loop network constraints for optimization; AC power flow used for validation of voltages/ampacity.
- **Validation and releases**:
  - BP1 target: HydroFlex `v0.x`, validated hydro model behavior, and functional test case with sizing/dispatch parity checks.
  - BP2 target: HydroFlex `v1.x`, case-study replication package, stakeholder-informed refinements, and public-facing summaries.
- **Analytics expectation**: report total cost decomposition, capital requirements, emissions impacts, ramping/peak-shaving, capacity factors, and benefit-risk checks for adopted assets.

## Working Agreement

- This file and `docs/deropt_python_pyomo_rebuild.md` should be updated together when scope/architecture decisions change.
- Slice execution should follow the ordered implementation table in the canonical plan.

