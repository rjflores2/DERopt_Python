"""Max-capability case: multi-node load, multi-profile solar, battery, diesel, multi-tariff utility."""

from pathlib import Path

from config.case_config import (
    CaseConfig,
    EnergyLoadFileConfig,
    FinancialsConfig,
    UtilityTariffConfig,
    discover_load_file,
    discover_solar_file,
)


def default_max_capability_case(project_root: Path) -> CaseConfig:
    """Return a full-featured local case config using data/MaxCapability."""
    data_dir = project_root / "data" / "MaxCapability"
    load_path = discover_load_file(data_dir)
    solar_path = discover_solar_file(data_dir)

    node_1 = "electricity_load__node_1_electric_kw"
    node_2 = "electricity_load__node_2_electric_kw"
    node_3 = "electricity_load__node_3_electric_kw"
    fixed = "solar_production__fixed_optimal"
    tracking = "solar_production__1d_tracking"
    flat = "solar_production__flat"

    return CaseConfig(
        case_name="Max Capability",
        energy_load=EnergyLoadFileConfig(
            csv_path=load_path,
            datetime_column="Date",
            datetime_format="auto",
            target_interval_minutes=60,
            interpolation_method="linear",
            treat_negative_as_missing=True,
            resample_only_if_irregular=True,
        ),
        solar_path=solar_path,
        technology_parameters={
            "solar_pv": {
                "allow_adoption": True,
                "efficiency": 0.20,
                "capital_cost_per_kw": 1700.0,
                "om_per_kw_year": 22.0,
                "params_by_profile": {
                    fixed: {"efficiency": 0.20, "capital_cost_per_kw": 1600.0, "om_per_kw_year": 20.0},
                    tracking: {"efficiency": 0.22, "capital_cost_per_kw": 2100.0, "om_per_kw_year": 25.0},
                    flat: {"efficiency": 0.18, "capital_cost_per_kw": 1400.0, "om_per_kw_year": 18.0},
                },
                "max_capacity_area_by_node_and_profile": {
                    node_1: {fixed: 1500.0, tracking: 1200.0, flat: 800.0},
                    node_2: {fixed: 1000.0, tracking: 700.0, flat: 500.0},
                    node_3: {fixed: 1800.0, tracking: 1400.0, flat: 900.0},
                },
                "existing_solar_capacity_by_node_and_profile": {
                    node_1: {fixed: 75.0, tracking: 0.0, flat: 0.0},
                    node_2: {fixed: 20.0, tracking: 15.0, flat: 0.0},
                    node_3: {fixed: 0.0, tracking: 0.0, flat: 10.0},
                },
                "use_marginal_capital_for_existing_recovery": True,
            },
            "battery_energy_storage": {
                "allow_adoption": True,
                "charge_efficiency": 0.95,
                "discharge_efficiency": 0.95,
                "capital_cost_per_kwh": 350.0,
                "om_per_kwh_year": 12.0,
                "max_charge_power_per_kwh": 0.5,
                "max_discharge_power_per_kwh": 0.5,
                "existing_energy_capacity_by_node": {
                    node_1: 120.0,
                    node_2: 80.0,
                    node_3: 0.0,
                },
                "initial_soc_fraction": 0.5,
            },
            "diesel_generator": {
                "allow_adoption": True,
                "formulation": "diesel_binary",
                "capital_cost_per_kw": 950.0,
                "fixed_om_per_kw_year": 18.0,
                "variable_om_per_kwh": 0.02,
                "electric_efficiency": 0.35,
                "minimum_loading_fraction": 0.30,
                "existing_capacity_by_node": {
                    node_1: 250.0,
                    node_2: 150.0,
                    node_3: 100.0,
                },
                "capacity_adoption_limit_by_node": {
                    node_1: 500.0,
                    node_2: 400.0,
                    node_3: 300.0,
                },
            },
        },
        financials=FinancialsConfig(
            debt_fraction=0.6,
            debt_years=12.0,
            debt_rate=0.075,
            equity_years=8.0,
            equity_rate=0.14,
            levelization_years=15.0,
        ),
        utility_tariffs=[
            UtilityTariffConfig(
                tariff_key="default_tou_dc",
                utility_rate_path=data_dir / "SCE_GS3_TOU.json",
                utility_rate_item_index=0,
                energy_price_path=data_dir / "node_default_price.csv",
                energy_price_column="price_usd_per_kwh",
            ),
            UtilityTariffConfig(
                tariff_key="alt_rate_node3",
                utility_rate_path=data_dir / "SCE_D_TOU.json",
                utility_rate_item_index=0,
            ),
        ],
        node_utility_tariff={
            node_3: "alt_rate_node3",
        },
    )
