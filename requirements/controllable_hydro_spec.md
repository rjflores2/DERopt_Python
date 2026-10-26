# Implementation Spec: `controllable_hydro` Technology Module

Status: not started. No stub exists. This is a new package under `technologies/`.

Build one module that covers both conventional dam hydro and pumped storage hydro. They share a single formulation. A unit with zero pump capacity is a conventional dam. Do not create two modules.

---

## 1. Read these first

Do not begin until you have read and understood the conventions in these files. The new module must match them.

| File | What to take from it |
|---|---|
| `model/contracts.py` | The exact block interface that `validate_technology_block_interface` enforces. |
| `model/core.py` | How blocks are registered, how balances sum `*_source_term` / `*_sink_term`, how the objective sums `objective_contribution`. |
| `shared/cost_helpers.py` | `attach_standard_cost_expressions`, which every block must use to build its cost expressions. |
| `technologies/battery_energy_storage/block.py` | The inventory balance pattern, including cyclic wraparound indexing. This module's balance is a direct extension of it. |
| `technologies/hydrokinetic/block.py` | The pattern for a block with its own technology set indexed `[node, tech, scenario, t]`, and how a per-site timeseries becomes a Pyomo `Param`. |
| `technologies/hydrokinetic/__init__.py` | The `register()` hook signature and the guard that returns `None` when required data is absent. |
| `technologies/hydrokinetic/reporting.py` | The reporting hook signature. |
| `technologies/__init__.py` | The `REGISTRY` list. |
| `shared/scenario_helpers.py` | `series_for_scenario`, used for per-scenario timeseries lookup with fallback to base. |

## 2. Package layout

Create `technologies/controllable_hydro/` containing:

```
__init__.py      register() hook, re-exports, formulation constants
inputs.py        DEFAULT_CONTROLLABLE_HYDRO_PARAMS, ResolvedControllableHydroInputs, resolver
block.py         add_controllable_hydro_block()
reporting.py     collect_block_report()
```

Add one entry to `REGISTRY` in `technologies/__init__.py`:

```python
("controllable_hydro", register_controllable_hydro),
```

Place it after `hydrokinetic`.

## 3. Indexing

Follow the pattern used by `hydrokinetic` and `solar_pv`, not the single-instance pattern used by the storage blocks.

Declare a block set:

```python
hydro_block.HYDRO = pyo.Set(initialize=unit_keys, ordered=True)
```

Dispatch variables are indexed `[node, unit, scenario, t]`. Capacity parameters are indexed `[node, unit]`. A unit is understood to be located at the node where its capacities are non-zero. Zero-capacity `(node, unit)` pairs are permitted and inert, exactly as in `solar_pv` where `existing_solar_capacity[node, profile]` may be zero.

Inflow is a property of the site, not of the node, so it is indexed `[unit, scenario, t]`, mirroring `hydrokinetic.yield_kwh_per_m2[profile, scenario, t]`.

Minimum release is a licence condition and does not vary by scenario. Index it `[unit, t]`.

## 4. Units

Everything is in kWh of stored energy potential. This is deliberate and matches how hydrogen storage is handled, where inventory is kWh H2 LHV and conversion to kg happens in post-processing.

- Inventory, inflow, and spill are in kWh of stored energy potential.
- Turbine generation and pump consumption are in kWh of electricity at the bus.
- Capacities are in kW. Multiply by `model.time_step_hours` wherever a kW capacity bounds a kWh-per-timestep flow.

Conversion from acre-feet or cfs to kWh happens in the data loader, not in the block. The block never sees volumetric units.

## 5. Formulation

Let $\widetilde{E}_{n,d}$ be reservoir energy capacity, a parameter. There is no adoption.

**Inventory balance**, for all `(node, unit, scenario, t)`:

$$E^{\mathrm{SOC}}_{n,d,s,t} = \alpha_{d} E^{\mathrm{SOC}}_{n,d,s,t-1} + \iota_{d,s,t} + \eta^{Pump}_{d} e^{Pump}_{n,d,s,t} - \frac{e^{Gen}_{n,d,s,t}}{\eta^{Gen}_{d}} - \varsigma_{n,d,s,t}$$

Inflow $\iota$ is a parameter. Spill $\varsigma$ is a non-negative variable with no cost and no upper bound.

**Inventory window**, for all `(node, unit, scenario, t)`:

$$\underline{\sigma}_{d} \widetilde{E}_{n,d} \leq E^{\mathrm{SOC}}_{n,d,s,t} \leq \overline{\sigma}_{d} \widetilde{E}_{n,d}$$

**Turbine limit**, for all `(node, unit, scenario, t)`:

$$e^{Gen}_{n,d,s,t} \leq P^{Gen}_{n,d} \cdot \Delta$$

**Pump limit**, for all `(node, unit, scenario, t)`:

$$e^{Pump}_{n,d,s,t} \leq P^{Pump}_{n,d} \cdot \Delta$$

**Minimum release**, for all `(node, unit, scenario, t)`. Both terms are in stored energy potential, so turbine release enters divided by turbine efficiency:

$$\frac{e^{Gen}_{n,d,s,t}}{\eta^{Gen}_{d}} + \varsigma_{n,d,s,t} \geq \underline{\Phi}_{d,t}$$

Skip this constraint entirely for `(unit, t)` pairs where $\underline{\Phi}_{d,t}$ is zero or unset, rather than adding a trivially satisfied row.

Do not add a constraint preventing simultaneous pumping and generation. It is never optimal under round trip losses at a single nodal price, and permitting it correctly represents plants with separate pump and turbine units that must release for minimum flow while also storing.

## 6. Boundary condition

A configuration parameter `boundary_condition` selects one of three behaviours. This is a required parameter with no silent default, because the choice materially changes results for seasonal storage.

**`"cyclic"`** — The inventory balance applies at every `t`, and `t-1` refers to the last timestep of the horizon when `t` is the first. This is the same wraparound used in `battery_energy_storage/block.py`. Copy that indexing exactly.

**`"fixed_endpoints"`** — The balance applies for `t >= 2` only. Add two equalities fixing inventory at the first and last timesteps to `start_soc_fraction` and `end_soc_fraction` times capacity.

**`"free_end"`** — The balance applies for `t >= 2` only. Add one equality fixing inventory at the first timestep. Leave the final level unconstrained.

Validate that any specified SOC fraction lies within `[minimum_soc_fraction, maximum_soc_fraction]` and raise a clear `ValueError` at input-resolution time if it does not. An out-of-window endpoint produces an infeasible model with no useful diagnostic otherwise.

## 7. Cost

Hydro capacity is existing only. Call `attach_standard_cost_expressions` with `allow_adoption=False`, which sets `annual_capital_costs` and `fixed_om_adopted` to zero automatically.

- `variable_operating_cost` = sum over `(node, unit, scenario, t)` of `scenario_probability[s] * variable_om_per_kwh[d] * turbine_generation[n,d,s,t]`
- `fixed_om_existing` = sum over `(node, unit)` of `fixed_om_per_kw_year[d] * turbine_capacity_kw[n,d]`

Pump electricity is not a cost term. It is priced through the electricity balance. Spill is free.

Do not use `extra_objective_terms`. Nothing in the repo uses it and it bypasses the named reporting components.

## 8. Balance terms

```python
hydro_block.electricity_source_term = pyo.Expression(
    nodes, model.SCENARIOS, T,
    rule=lambda m, node, s, t: sum(m.turbine_generation[node, d, s, t] for d in m.HYDRO),
)
hydro_block.electricity_sink_term = pyo.Expression(
    nodes, model.SCENARIOS, T,
    rule=lambda m, node, s, t: sum(m.pump_consumption[node, d, s, t] for d in m.HYDRO),
)
```

Define no hydrogen terms.

## 9. Data contract

The `register()` hook returns `None` unless both of the following hold, matching the guard in `hydrokinetic/__init__.py`:

- `data.static["controllable_hydro_unit_keys"]` is non-empty
- `technology_parameters["controllable_hydro"]` is not `None`

Required on `data`:

| Key | Location | Meaning |
|---|---|---|
| `controllable_hydro_unit_keys` | `data.static` | Ordered list of unit identifiers, used to initialize `HYDRO`. |
| One series per unit | `data.timeseries[<inflow_key>]` | Inflow in kWh of stored energy potential per timestep. |
| `controllable_hydro_inflow_keys` | `data.static` | Maps unit key to its inflow series key. |

Resolve per-scenario inflow with `series_for_scenario(data, s, inflow_key)` so that a scenario without its own inflow override falls back to the base series. Validate that every resolved series has length equal to the horizon and raise with the unit key and both lengths named, following the validation style in `hydrokinetic/block.py`.

A loader is also needed. Add a function to `data_loading/loaders/` that reads a volumetric inflow series in cfs or acre-feet, converts it to kWh using a supplied `kwh_per_acre_foot` coefficient, aligns it to the container time index, and writes the result plus the two static keys. Multiply by `time_step_hours` where the source is a rate rather than a volume, matching how `resource_profiles.py` handles capacity factors.

## 10. Parameters

`DEFAULT_CONTROLLABLE_HYDRO_PARAMS`. Values marked "no default" must raise if absent, because a wrong guess is worse than a failure.

| Parameter | Default | Notes |
|---|---|---|
| `allow_adoption` | `False` | Must remain `False`. Present only for interface symmetry. |
| `boundary_condition` | no default | One of `cyclic`, `fixed_endpoints`, `free_end`. |
| `turbine_efficiency` | no default | Stored potential to bus electricity. |
| `pump_efficiency` | `0.0` | Zero disables pumping. |
| `inventory_retention` | `1.0` | Evaporation and seepage per timestep. Default to lossless deliberately. |
| `minimum_soc_fraction` | `0.0` | |
| `maximum_soc_fraction` | `1.0` | |
| `start_soc_fraction` | `None` | Required for `fixed_endpoints` and `free_end`. |
| `end_soc_fraction` | `None` | Required for `fixed_endpoints`. |
| `reservoir_capacity_kwh_by_node_unit` | no default | |
| `turbine_capacity_kw_by_node_unit` | no default | |
| `pump_capacity_kw_by_node_unit` | `0.0` | Zero gives a conventional dam. |
| `minimum_release_kwh_by_unit_time` | `None` | Sparse. Omitted pairs get no constraint. |
| `variable_om_per_kwh` | `0.0` | |
| `fixed_om_per_kw_year` | `0.0` | |

Set `inventory_retention` to `1.0` by default rather than a value slightly below one. The hydrogen storage block currently defaults to `0.9999`, which compounds to a 58 percent annual loss and makes seasonal storage look uneconomic for a non-physical reason. Do not repeat that here.

## 11. Reporting hook

Implement `collect_block_report` following `hydrokinetic/reporting.py`. Emit at minimum:

- `turbine_capacity_factor` — probability-weighted generation over nameplate times horizon
- `total_spill_kwh` — probability-weighted spill, which is the primary diagnostic for whether the reservoir is oversubscribed
- `spill_fraction` — spill over total inflow
- `minimum_inventory_reached` and `maximum_inventory_reached` as fractions of capacity
- `pump_energy_kwh` and `round_trip_efficiency` where pump capacity is non-zero

Return `{}` if the expected components are absent, matching the existing guard style.

## 12. Tests

Add `tests/test_controllable_hydro_block.py`. Build small models directly, following the fixture style in `tests/test_hydrokinetic_block.py` and `tests/test_technology_block_contract.py`.

1. **Contract.** The block passes `validate_technology_block_interface`, and `register()` returns the same object attached as `model.controllable_hydro`.
2. **Registry guard.** `register()` returns `None` when `controllable_hydro_unit_keys` is missing, and again when the config value is `None`.
3. **Inventory conservation.** Over a short horizon with known inflow and forced dispatch, final inventory equals the balance prediction to within solver tolerance.
4. **Cyclic closure.** Under `cyclic`, inventory at the first timestep equals the value implied by the last timestep and the first-timestep flows.
5. **Spill activates.** Inflow far exceeding turbine capacity and reservoir headroom yields a feasible solve with positive spill. This is the test that proves the module does not go infeasible in a wet period.
6. **Spill is required for feasibility.** The same case with spill fixed to zero is infeasible. This guards against a future refactor silently dropping the spill variable.
7. **Minimum release binds.** With a minimum release above what the model would otherwise choose, the solution meets it, using spill when generation alone would overproduce.
8. **Conventional dam.** With `pump_capacity_kw` zero, all `pump_consumption` values are zero and the electricity sink term is zero.
9. **Pumped storage loses energy.** A round trip through pump and turbine returns less electricity than it consumed, at the product of the two efficiencies.
10. **Boundary conditions differ.** The same case under `free_end` reports a cost no higher than under `cyclic`.
11. **Sub-hourly timestep.** A run with `time_step_hours = 0.25` scales all capacity limits correctly and does not change the energy result relative to an equivalent hourly case.
12. **Scenario handling.** Capacity parameters are shared across scenarios while dispatch and inventory vary, and the objective is probability-weighted.
13. **Input validation.** An out-of-window `start_soc_fraction` raises `ValueError` at resolution time, not at solve time.

## 13. Non-goals

Do not implement any of the following. Each is a deliberate scope exclusion.

- Hydro capacity adoption or sizing. All hydro capacity is existing.
- Head-dependent conversion efficiency. Constant head is assumed.
- Cascaded reservoirs. No hydraulic arc set, no transit delay.
- A lower reservoir for pumped storage. Only the upper reservoir is tracked.
- Spillway capacity limits. Spill is unbounded, which is what guarantees the cyclic case is always feasible.
- Ramp limits on turbine output. Wanted later, not now.
- Unit commitment on hydro turbines. The linear turbine limit is sufficient for the first version. If added later it reuses the three formulations in `diesel_generator/inputs.py` rather than inventing new ones.
- Volumetric state variables. The block works in kWh only.

## 14. Confirm before building

Two items need a decision from Robert and should not be guessed.

**Where the volume to energy coefficient lives.** This spec puts it in the loader so the block never sees volume. Confirm that, and confirm the coefficient is set at the upper end of the operating head band. A coefficient fixed at a lower head causes the minimum release constraint to be satisfied in energy terms while releasing less water than the licence requires.

**Whether `fixed_om_existing` should include a reservoir term.** As specified it is charged on turbine kW only. If reservoir fixed O&M is material it needs a second term on energy capacity. Note this figure is reported rather than optimized, so it does not affect the solution either way.

## 15. Known repo defects to avoid copying

Two patterns in the existing blocks should not be reproduced here.

`diesel_generator/block.py` declares `capacity_adoption_limit` and `unit_adoption_limit` as parameters and never constrains anything with them, and its `maximum_total_units` expression is unused. Adoption is unbounded there as a result. Not directly relevant since this module has no adoption, but do not imitate the pattern of declaring a limit parameter without a constraint.

`shared/cost_helpers.py` exposes a `dt_hours` argument on `time_summed_variable_cost` that defaults to `1.0` and multiplies the flow variable. Flow variables are already in kWh per timestep, so passing anything else double counts the timestep duration. Do not pass it.
