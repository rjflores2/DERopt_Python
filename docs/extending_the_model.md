# Extending The Model

This guide explains how to add new technologies and features without changing core architecture.

## Core Principle

`model/core.py` is a registry-based assembler. It should not contain technology-specific formulas.  
Each technology module in `technologies/` owns:

- parameter contract (`tech_params[tech_name]`)
- variables and constraints
- objective/cost contribution
- supply/load terms registered with core balances

## Add A New Technology

1. Create `technologies/<new_technology>.py`.
2. Define required parameters in `tech_params["<new_technology>"]`.
3. Add Pyomo variables and constraints inside the technology block.
4. Define objective contribution (capex annuity, fixed/variable O&M, fuel if needed).
5. Expose balance terms:
   - electricity/thermal/H2 supply terms
   - electricity/thermal/H2 load terms
6. Register those terms with core through the existing technology registry interface.
7. Add config toggle so the technology can be enabled/disabled per case.
8. Add unit/regression tests.

## Config-Driven Inclusion

Technologies should be turned on/off by case config, not by branching model code.

- Example: PV off -> `solar_pv` block is not attached.
- Example: PV on -> `solar_pv` block is attached and contributes variables/constraints/cost.

## Data Contract Expectations

Loaders should build one validated data container:

- `indices`
- `timeseries` (including canonical `time_serial[t]`)
- `static`
- `tech_params`

Technology modules read from `tech_params` and relevant shared series, but do not parse raw files directly.

## Adding Non-Technology Features

Use `utilities/` for tariff/grid logic (import/export, demand charges, network).  
Do not place utility logic in technology files.

## Validation Workflow

- Optimization uses tractable in-loop constraints (core + tech + utility/network modules).
- AC power flow tooling (e.g., OpenDSS/PyPower) is for post-optimization validation checks (voltage/feasibility), not in-loop MILP/LP formulation.

