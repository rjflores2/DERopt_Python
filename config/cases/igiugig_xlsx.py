"""Igiugig xlsx case: off-grid microgrid baseline from Flores et al. (UCI manuscript v4).

Reproduces the baseline parameter set in "Optimizing Hydrokinetic, Solar, Battery, and
Hydrogen Storage Technologies for Resilient Microgrids in Remote Regions" (Tables 1 and 3).

Default scenario: MILP, diesel-available, HKT at $4,000/kW, HKT limited to two 80 kW units
per site (Table 6, row 2). Set ``FORMULATION = "lp"`` below to run the LP marginal-value
version instead (Section 4.1 / Figure 4).

Parameter mapping notes (paper -> DERopt_Python):

* Financing. Eq. (1) uses a single capital recovery factor at i = 6% over n = 10 years.
  ``FinancialsConfig`` splits capital into debt and equity, so debt_fraction is set to 1.0
  with a 10 year, 6% debt tranche and a 10 year levelization. The resulting amortization
  factor is CRF(0.06, 10) = 0.135868, identical to Eq. (1).

* Electrolyzer basis. The paper prices the electrolyzer per kW of H2 chemical energy
  output ($2,100/kW-H2, $0.01/kWh-H2). This model sizes the electrolyzer on electric
  input kW, so both are multiplied by the 0.6 kWh-H2/kWh-electric conversion efficiency:
  $2,100 x 0.6 = $1,260/kW-electric and $0.01 x 0.6 = $0.006/kWh-electric. Reported
  electrolyzer capacity from this model is therefore kW-electric; multiply by 0.6 to
  compare against the kW-H2 values in Tables 5 and 7.

* Solar capacity limit. Eq. (8) caps solar at 2 MW. The solar block expresses its cap as a
  land area limit through ``capacity_kW / efficiency <= max_capacity_area``. Setting
  efficiency to 1.0 makes the area limit a direct kW cap of 2,000 kW. Efficiency is not
  used anywhere else in the block; the solar profiles are already kWh per kW installed.

* Legacy diesel is treated as sunk. Diesel capital and fixed O&M are zero and adoption is
  disabled, which reproduces the Table 5 "Legacy Diesel + Battery" LCOE of $0.827/kWh
  (fuel $0.805/kWh at $10/gal and 33% efficiency, plus $0.02/kWh variable O&M).

* H2 storage charging. The paper's "H2 storage charging efficiency" of 3/38.9 is compressor
  electricity per unit of H2 charged, so it maps to
  ``compression_kwh_electric_per_kwh_h2_lhv`` rather than to a charge efficiency. Charge
  and discharge efficiencies are therefore 1.0.

Parameters in the paper with no equivalent in this model, set to zero here:

* Battery O&M of $0.001/kWh charged and discharged (block has fixed $/kWh-year only).
* Solar PV variable O&M of $0.001/kWh (block has fixed $/kW-year only).
* H2 storage variable O&M of $0.01/kWh charged and $0.001/kWh discharged.

Assumption not stated in the paper:

* Existing diesel unit count is 3 x 50 kW. Peak community load is 109.5 kW, so three units
  cover peak with one unit of reserve. Adjust ``EXISTING_DIESEL_UNITS`` if the real
  Igiugig fleet differs.

Run with: DEROPT_CASE=Igiugig_xlsx python -m run.playground
"""

from pathlib import Path

from config.case_config import (
    CaseConfig,
    EnergyLoadFileConfig,
    FinancialsConfig,
    discover_hydrokinetic_file,
    discover_load_file,
    discover_solar_file,
)

# ---------------------------------------------------------------------------
# Scenario switches
# ---------------------------------------------------------------------------

# "milp" -> discrete HKT units, diesel unit commitment, electrolyzer/PEMFC part-load limits.
# "lp"   -> continuous swept area and capacity, no part-load limits (marginal value runs).
FORMULATION = "milp"

# Representative HKT equipment costs from Section 4.1: 8000, 4000, or 2000 $/kW.
HKT_CAPITAL_COST_PER_KW = 4000.0

# Table 6 limits HKT to two 80 kW units per site. Set to None for the unconstrained (*) cases.
MAX_HKT_UNITS_PER_SITE: int | None = 4

# Set False for the diesel-free designs in Table 7.
DIESEL_AVAILABLE = True

# 3 x 50 kW covers the 109.5 kW peak load with one unit of reserve.
EXISTING_DIESEL_UNITS = 3

# Peak community load (kW) from data/Igiugig_xlsx/Igiugig_Electric_Loads.xlsx.
PEAK_LOAD_KW = 109.5

# IMPORTANT: pem_electrolyzer and pem_fuel_cell default
# ``capacity_adoption_limit_kw_by_node`` to 0.0, and their blocks enforce
# ``capacity_adopted_kw <= capacity_adoption_limit_kw``. Setting allow_adoption=True without
# also setting an explicit limit therefore pins H2 capacity to zero silently, with no error
# and no warning. The limits below follow the paper's own big-M reasoning in Section 3.2:
# the fuel cell is assumed not to exceed peak load, and the electrolyzer is allowed up to ten
# times peak load to accommodate a large PV or HKT array. Do not remove these.
PEMFC_ADOPTION_LIMIT_KW = PEAK_LOAD_KW
ELECTROLYZER_ADOPTION_LIMIT_KW = 10.0 * PEAK_LOAD_KW

# ---------------------------------------------------------------------------
# Physical constants and reference device
# ---------------------------------------------------------------------------

# Commercial HKT at Igiugig: 80 kW rated, 18 m^2 swept area (Section 3.3).
HKT_REFERENCE_KW = 80.0
HKT_REFERENCE_SWEPT_AREA_M2 = 18.0

# Table 1 efficiencies.
ELECTROLYZER_LHV_EFFICIENCY = 0.6      # kWh-H2 per kWh-electric
PEMFC_LHV_EFFICIENCY = 0.5             # kWh-electric per kWh-H2
DIESEL_ELECTRIC_EFFICIENCY = 0.33
H2_COMPRESSION_KWH_E_PER_KWH_H2 = 3.0 / 38.9

# Paper prices the electrolyzer on H2 chemical energy output; convert to electric input.
ELECTROLYZER_CAPITAL_PER_KW_H2 = 2100.0
ELECTROLYZER_VOM_PER_KWH_H2 = 0.01

# ---------------------------------------------------------------------------
# Timeseries keys (derived by the loaders from the data file column headers)
# ---------------------------------------------------------------------------

NODE = "electricity_load__electric_load_kw"
SOLAR_FIXED = "solar_production__fixed_kw"
SOLAR_TRACKING = "solar_production__tracking_kw"
HKT_SITE_1 = "hydrokinetic_production__site_1_historic_average_power_kw"
HKT_SITE_2 = "hydrokinetic_production__site_2_historic_average_power_kw"


def _formulation(lp_name: str, milp_name: str) -> str:
    if FORMULATION not in ("lp", "milp"):
        raise ValueError(f"FORMULATION must be 'lp' or 'milp', got {FORMULATION!r}")
    return lp_name if FORMULATION == "lp" else milp_name


def _technology_parameters() -> dict[str, dict]:
    """Baseline technology parameters from Tables 1 and 3 of the manuscript."""
    hkt_unit_limits = (
        None
        if MAX_HKT_UNITS_PER_SITE is None
        else {NODE: {HKT_SITE_1: MAX_HKT_UNITS_PER_SITE, HKT_SITE_2: MAX_HKT_UNITS_PER_SITE}}
    )

    params: dict[str, dict] = {
        # -------------------------------------------------------------------
        # Solar PV — two installation types (Eq. 7, 8)
        # -------------------------------------------------------------------
        "solar_pv": {
            "allow_adoption": True,
            # 1.0 so the area cap below acts as a direct kW cap (see module docstring).
            "efficiency": 1.0,
            "capital_cost_per_kw": 3200.0,
            "om_per_kw_year": 0.0,
            "params_by_profile": {
                SOLAR_FIXED: {"capital_cost_per_kw": 3200.0, "efficiency": 1.0, "om_per_kw_year": 0.0},
                SOLAR_TRACKING: {"capital_cost_per_kw": 2520.0, "efficiency": 1.0, "om_per_kw_year": 0.0},
            },
            # PV_max = 2 MW per installation type.
            "max_capacity_area_by_node_and_profile": {
                NODE: {SOLAR_FIXED: 2000.0, SOLAR_TRACKING: 2000.0},
            },
        },
        # -------------------------------------------------------------------
        # Hydrokinetic turbines — Site 1 and Site 2 (Eq. 5, 6 / Eq. 20, 21)
        # -------------------------------------------------------------------
        "hydrokinetic": {
            "allow_adoption": True,
            "formulation": _formulation("hydrokinetic_lp", "hydrokinetic_unit_milp"),
            "capital_cost_per_kw": HKT_CAPITAL_COST_PER_KW,
            "capital_cost_per_m2": 0.0,
            "fixed_om_per_kw_year": 0.0,
            "fixed_om_per_m2_year": 0.0,
            "variable_om_per_kwh": 0.01,
            "unit_capacity_kw": HKT_REFERENCE_KW,
            "unit_swept_area_m2": HKT_REFERENCE_SWEPT_AREA_M2,
            "fixed_om_per_unit_year": 0.0,
            # LP only: ties adopted kW to swept area at the commercial device's density
            # (80 kW / 18 m^2) so the solver cannot inflate area to relax Eq. (5).
            "max_power_density_kw_per_m2": HKT_REFERENCE_KW / HKT_REFERENCE_SWEPT_AREA_M2,
            **({"max_installed_units_by_node_and_profile": hkt_unit_limits} if hkt_unit_limits else {}),
        },
        # -------------------------------------------------------------------
        # Battery energy storage (Eq. 9-13)
        # -------------------------------------------------------------------
        "battery_energy_storage": {
            "allow_adoption": True,
            "charge_efficiency": 0.90,
            "discharge_efficiency": 0.90,
            "capital_cost_per_kwh": 830.0,
            # Paper's $0.001/kWh throughput O&M has no variable term in this block.
            "om_per_kwh_year": 0.0,
            "max_charge_power_per_kwh": 0.5,
            "max_discharge_power_per_kwh": 0.5,
            "state_of_charge_retention": 0.9999,
            "minimum_state_of_charge": 0.1,
            "maximum_state_of_charge": 0.9,
            # Existing 250 kWh community battery.
            "existing_energy_capacity_by_node": {NODE: 250.0},
        },
        # -------------------------------------------------------------------
        # PEM electrolyzer (Eq. 14, 25, 26)
        # -------------------------------------------------------------------
        "pem_electrolyzer": {
            "allow_adoption": True,
            "formulation": _formulation("pem_electrolyzer_lp", "pem_electrolyzer_binary"),
            "capital_cost_per_kw": ELECTROLYZER_CAPITAL_PER_KW_H2 * ELECTROLYZER_LHV_EFFICIENCY,
            "fixed_om_per_kw_year": 0.0,
            "variable_om_per_kwh_electric": ELECTROLYZER_VOM_PER_KWH_H2 * ELECTROLYZER_LHV_EFFICIENCY,
            "electric_to_hydrogen_lhv_efficiency": ELECTROLYZER_LHV_EFFICIENCY,
            # 30% minimum H2 output when on; 0 in the LP.
            "minimum_loading_fraction": 0.0 if FORMULATION == "lp" else 0.30,
            # Big-M is ten times the maximum community load (Section 3.2).
            "electrolyzer_binary_big_m_load_multiplier": 10.0,
            # Required: defaults to 0.0, which would silently forbid adoption.
            "capacity_adoption_limit_kw_by_node": {NODE: ELECTROLYZER_ADOPTION_LIMIT_KW},
        },
        # -------------------------------------------------------------------
        # PEM fuel cell (Eq. 15, 23, 24)
        # -------------------------------------------------------------------
        "pem_fuel_cell": {
            "allow_adoption": True,
            "formulation": _formulation("pem_fuel_cell_lp", "pem_fuel_cell_binary"),
            "capital_cost_per_kw": 2000.0,
            "fixed_om_per_kw_year": 0.0,
            "variable_om_per_kwh_electric": 0.01,
            "hydrogen_lhv_to_electric_efficiency": PEMFC_LHV_EFFICIENCY,
            # 50% minimum electrical output when on; 0 in the LP. Big-M is the peak load,
            # which the block derives from the load series automatically.
            "minimum_loading_fraction": 0.0 if FORMULATION == "lp" else 0.50,
            # Required: defaults to 0.0, which would silently forbid adoption.
            "capacity_adoption_limit_kw_by_node": {NODE: PEMFC_ADOPTION_LIMIT_KW},
        },
        # -------------------------------------------------------------------
        # Compressed gas H2 storage (Eq. 16, 17)
        # -------------------------------------------------------------------
        "compressed_gas_hydrogen_storage": {
            "allow_adoption": True,
            "capital_cost_per_kwh_h2_lhv": 25.0,
            # Paper's $0.01/kWh charge and $0.001/kWh discharge O&M have no variable term here.
            "om_per_kwh_h2_lhv_year": 0.0,
            # The paper's charging "efficiency" is compressor parasitic load, applied below.
            "charge_efficiency": 1.0,
            "discharge_efficiency": 1.0,
            "compression_kwh_electric_per_kwh_h2_lhv": H2_COMPRESSION_KWH_E_PER_KWH_H2,
            # Negligible self-discharge for compressed gas storage.
            "hydrogen_inventory_retention": 1.0,
            "minimum_hydrogen_inventory_fraction": 0.0,
            "maximum_hydrogen_inventory_fraction": 1.0,
            "max_hydrogen_charge_per_kwh_capacity": 1.0,
            "max_hydrogen_discharge_per_kwh_capacity": 1.0,
        },
    }

    # -----------------------------------------------------------------------
    # Legacy diesel generation (Eq. 22)
    # -----------------------------------------------------------------------
    if DIESEL_AVAILABLE:
        params["diesel_generator"] = {
            # Legacy fleet only; no new diesel adoption in any paper scenario.
            "allow_adoption": False,
            "formulation": _formulation("diesel_lp", "diesel_unit_milp"),
            # Sunk capital: the paper charges only fuel and variable O&M to legacy diesel.
            "capital_cost_per_kw": 0.0,
            "capital_cost_per_unit": 0.0,
            "fixed_om_per_kw_year": 0.0,
            "fixed_om_per_unit_year": 0.0,
            "variable_om_per_kwh": 0.02,
            "fuel_cost_per_gallon": 10.0,
            "electric_efficiency": DIESEL_ELECTRIC_EFFICIENCY,
            # 25 kW minimum / 50 kW maximum per operating generator (Table 3).
            "unit_capacity_kw": 50.0,
            "minimum_loading_fraction": 0.0 if FORMULATION == "lp" else 0.50,
            "existing_unit_count_by_node": {NODE: EXISTING_DIESEL_UNITS},
            "existing_capacity_by_node": {NODE: 50.0 * EXISTING_DIESEL_UNITS},
        }

    return params


def default_igiugig_xlsx_case(project_root: Path) -> CaseConfig:
    """Return the Igiugig baseline case config matching the manuscript parameters."""
    folder = (project_root / "data" / "Igiugig_xlsx").resolve()
    load_path = discover_load_file(folder)
    solar_path = discover_solar_file(folder)
    hydrokinetic_path = discover_hydrokinetic_file(folder)

    if solar_path is None:
        raise FileNotFoundError(f"Expected a solar resource file in {folder}")
    if hydrokinetic_path is None:
        raise FileNotFoundError(f"Expected a hydrokinetic resource file in {folder}")

    return CaseConfig(
        case_name="Igiugig xlsx",
        energy_load=EnergyLoadFileConfig(csv_path=load_path),
        solar_path=solar_path,
        # Wind is not part of the manuscript's optimization scope; data/wind.csv is ignored.
        wind_path=None,
        hydrokinetic_path=hydrokinetic_path,
        # hkt_potential.xlsx holds daily average power for the 80 kW / 18 m^2 commercial
        # device; both references are required to convert kWh/kW into kWh/m^2 (Eq. 5).
        hydrokinetic_reference_kw=HKT_REFERENCE_KW,
        hydrokinetic_reference_swept_area_m2=HKT_REFERENCE_SWEPT_AREA_M2,
        technology_parameters=_technology_parameters(),
        # Eq. (1): 6% interest over 10 years, financed entirely as debt so the
        # amortization factor equals CRF(0.06, 10) = 0.135868.
        financials=FinancialsConfig(
            debt_fraction=1.0,
            debt_years=10.0,
            debt_rate=0.06,
            equity_years=10.0,
            equity_rate=0.06,
            levelization_years=10.0,
        ),
        # Off-grid community: no utility interconnection, so no tariff is loaded.
        utility_mode="islanded",
        utility_rate_path=None,
    )


def default_igiugig_diesel_on_case(project_root: Path) -> CaseConfig:
    """Full technology stack (solar, HKT, battery, H2 storage) with legacy diesel available.

    Explicitly sets ``DIESEL_AVAILABLE`` rather than relying on the module's current default,
    so this case is self-contained regardless of call order relative to
    ``default_igiugig_diesel_off_case`` (both mutate the same module-level switch). Mirrors
    ``scripts/run_paper_cases.py``'s ``diesel_on`` case, exposed as its own case name
    (``DEROPT_CASE=igiugig_diesel_on``) for a single-case run via ``run/playground.py``.
    """
    global DIESEL_AVAILABLE
    DIESEL_AVAILABLE = True
    return default_igiugig_xlsx_case(project_root)


def default_igiugig_diesel_off_case(project_root: Path) -> CaseConfig:
    """Same technology stack minus diesel (solar, HKT, battery, H2 storage only).

    Mirrors ``scripts/run_paper_cases.py``'s ``diesel_off`` case. See
    ``default_igiugig_diesel_on_case`` for why ``DIESEL_AVAILABLE`` is set explicitly here.
    """
    global DIESEL_AVAILABLE
    DIESEL_AVAILABLE = False
    return default_igiugig_xlsx_case(project_root)
