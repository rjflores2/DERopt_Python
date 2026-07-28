"""Stochastic Demo case: single-node, islanded diesel + hydrokinetic + solar + wind
microgrid with a two-stage stochastic "resource year" decision (wet / typical / dry).

Purpose: a small, hand-inspectable case to exercise ``CaseConfig.scenarios`` end to end,
including the same override mechanism applied to three different resource types at once
(``hydrokinetic_path``, ``solar_path``, ``wind_path`` are all independent per-scenario
override fields on ``ScenarioConfig`` with identical semantics -- see each one below).
All four technologies' capacity is fixed (``allow_adoption: False``, existing capacity
only) -- no Stage-1 investment decision here, deliberately, to keep the case robust and
its economics easy to reason about: hydrokinetic is sized so it is genuinely
resource-constrained (it cannot cover the load alone even in the wet scenario at every
hour), so diesel dispatch is a real Stage-2 recourse decision that differs sharply by
scenario, while all four technologies' capacities are the SAME shared numbers in every
scenario. See the bottom of this file for how to turn a technology's capacity into an
actual Stage-1 decision instead.

The three scenarios tell one coherent weather story rather than varying each resource
independently, using the SAME 1.0x / 1.5x / 0.35x multiplier convention throughout, just
applied in the physically sensible direction per resource:
  - "wet"     -- stormy: high river flow AND high wind, but cloudy (low solar).
  - "typical" -- baseline for all three resources.
  - "dry"     -- clear/calm: low river flow AND low wind, but sunny (high solar).
This makes solar a genuine hedge against hydrokinetic/wind in the dry scenario (and vice
versa in wet) -- a small illustration of why a diversified renewable portfolio reduces
weather-year risk. It is a deliberate narrative choice for this demo, not a physical
requirement: real scenarios need not correlate resources this way, and each of
hydrokinetic_path/solar_path/wind_path can be set totally independently per scenario.

Data: data/Stochastic_Demo/ — two representative days, hourly (48 steps):
  - Stochastic_Demo_Electric_Loads.csv: diurnal community load, same across all scenarios
    (peaks in the evening, after solar has set -- so solar alone never covers peak load,
    regardless of scenario).
  - hydrokinetic_typical.csv / _wet.csv / _dry.csv: in-stream flow (kW) for a reference
    80 kW / 18 m^2 device, at 1.0x / 1.5x / 0.35x the typical level.
  - solar_typical.csv / _wet.csv / _dry.csv: capacity factor (0-1) for a reference 1 kW
    panel, at 1.0x / 0.35x / 1.5x the typical level (inverted vs. hydrokinetic/wind --
    cloudy in "wet", clear in "dry").
  - wind_typical.csv / _wet.csv / _dry.csv: capacity factor (0-1) for a reference 1 kW
    turbine, at 1.0x / 1.5x / 0.35x the typical level (same direction as hydrokinetic).
  All three scenarios set their own hydrokinetic_path/solar_path/wind_path (required: each
  override field must be set on ALL scenarios or NONE), so none of them silently falls
  back to the base series.

Kept intentionally short (48 h, not a full 8760 h year) so it solves in well under a
second on Gurobi's free size-limited license. A short horizon is fine here specifically
because there's no capital/Stage-1 decision to size against a full year's economics (see
above) -- if you flip any technology to allow_adoption: True, capital cost is ALWAYS an
annualized (per-year) figure regardless of how many hours you represent, so a short
horizon will make any capital investment look worth far more (or less) than it really is
relative to a full year of avoided fuel. Extend the horizon (more CSV rows, same format)
before trusting a capacity-sizing answer.

Run with: DEROPT_CASE=Stochastic_Demo python -m run.playground
"""

from pathlib import Path

from config.case_config import CaseConfig, EnergyLoadFileConfig, ScenarioConfig


def default_stochastic_demo_case(project_root: Path) -> CaseConfig:
    folder = (project_root / "data" / "Stochastic_Demo").resolve()
    # Derived by the loader from the load CSV's column header ("Electric Demand (kW)"),
    # which normalizes to this key (see data_loading.loaders.energy_load._normalize_series_key
    # -- it deliberately maps "demand" -> "load" since the model works in kWh = load).
    node = "electricity_load__electric_load_kw"

    # Reference hydrokinetic device the resource CSVs were measured against (kW rated,
    # m^2 swept area); both are required so the model can convert kWh/kW -> kWh/m^2.
    reference_kw = 80.0
    reference_swept_area_m2 = 18.0
    # Existing (fixed) hydrokinetic device: smaller than the reference, at the same
    # density, so it's genuinely resource-limited rather than nameplate-limited most
    # hours -- the point is for wet/typical/dry to actually produce different dispatch.
    existing_hydrokinetic_kw = 30.0
    existing_hydrokinetic_area_m2 = existing_hydrokinetic_kw * reference_swept_area_m2 / reference_kw

    # solar/wind columns are already capacity factor (0-1) per kW, so no reference_kw-style
    # scaling is needed (unlike hydrokinetic, whose CSV is absolute kW for an 80 kW device).
    # Profile keys are derived by the loader from each CSV's column header ("Solar Site A
    # (kW/kW)" / "Wind Site A (kW/kW)"), same normalization as the load column above.
    solar_profile = "solar_production__solar_site_a_kw_kw"
    wind_profile = "wind_production__wind_site_a_kw_kw"
    existing_solar_kw = 15.0
    existing_wind_kw = 20.0

    return CaseConfig(
        case_name="Stochastic Demo",
        energy_load=EnergyLoadFileConfig(
            csv_path=folder / "Stochastic_Demo_Electric_Loads.csv"
        ),
        # Base-level hydrokinetic/solar/wind loads establish data.static metadata
        # (production key names, and for hydrokinetic, reference_kw/area) that each
        # technology block requires unconditionally; the three scenarios below override
        # the actual per-timestep series.
        hydrokinetic_path=folder / "hydrokinetic_typical.csv",
        hydrokinetic_reference_kw=reference_kw,
        hydrokinetic_reference_swept_area_m2=reference_swept_area_m2,
        solar_path=folder / "solar_typical.csv",
        wind_path=folder / "wind_typical.csv",
        technology_parameters={
            "diesel_generator": {
                "allow_adoption": False,
                # Sized to cover peak load alone, so the case stays feasible regardless of
                # resource year.
                "existing_capacity_by_node": {node: 60.0},
            },
            "hydrokinetic": {
                "allow_adoption": False,
                "formulation": "hydrokinetic_lp",
                "existing_capacity_kw_by_node_and_profile": {
                    (node, "hydrokinetic_production__river_site_a_kw"): existing_hydrokinetic_kw
                },
                "existing_swept_area_m2_by_node_and_profile": {
                    (node, "hydrokinetic_production__river_site_a_kw"): existing_hydrokinetic_area_m2
                },
            },
            "solar_pv": {
                "allow_adoption": False,
                "existing_solar_capacity_by_node_and_profile": {
                    (node, solar_profile): existing_solar_kw
                },
            },
            "wind_turbine": {
                "allow_adoption": False,
                "existing_wind_capacity_by_node_and_profile": {
                    (node, wind_profile): existing_wind_kw
                },
            },
        },
        # No grid connection: hydrokinetic/solar/wind vs. diesel is the whole story.
        utility_mode="islanded",
        scenarios=[
            ScenarioConfig(
                scenario_key="wet",
                probability=0.3,
                hydrokinetic_path=folder / "hydrokinetic_wet.csv",
                solar_path=folder / "solar_wet.csv",
                wind_path=folder / "wind_wet.csv",
            ),
            ScenarioConfig(
                scenario_key="typical",
                probability=0.4,
                hydrokinetic_path=folder / "hydrokinetic_typical.csv",
                solar_path=folder / "solar_typical.csv",
                wind_path=folder / "wind_typical.csv",
            ),
            ScenarioConfig(
                scenario_key="dry",
                probability=0.3,
                hydrokinetic_path=folder / "hydrokinetic_dry.csv",
                solar_path=folder / "solar_dry.csv",
                wind_path=folder / "wind_dry.csv",
            ),
        ],
    )


# To make hydrokinetic capacity an actual Stage-1 decision (the solver chooses how much
# to build, shared across all three scenarios) instead of a fixed given: set
# "allow_adoption": True and drop the two existing_* keys above. Two defaults will bite you
# if you do -- capital_cost_per_m2 defaults to 0.0 and max_swept_area_m2 defaults to 1e12
# (~unconstrained), which together make swept area a free variable: the solver has a
# standing incentive to inflate it far past what any chosen kW needs, relaxing the
# resource-availability constraint (generation <= area * yield_m2[t]) until it stops
# binding at all -- silently defeating the low-water-year resource limit rather than
# respecting it. Also set "max_power_density_kw_per_m2": reference_kw / reference_swept_area_m2
# (ties kW to a physically sensible area) and a nonzero "capital_cost_per_m2" (e.g. 200.0,
# so the solver has no reason to build more area than that density requires).
#
# solar_pv and wind_turbine are simpler to flip the same way: set "allow_adoption": True and
# drop their existing_*_capacity_by_node_and_profile key. Unlike hydrokinetic there's no area
# proxy to misuse, but capital_cost_per_kw defaults to a nonzero value for both (1500/kW solar,
# 1800/kW wind) so the solver isn't handed a free-capacity incentive by default the way an
# unset hydrokinetic capital_cost_per_m2 would.
