"""Primary execution entry point for local case runs."""

import os
from dataclasses import asdict
from pathlib import Path

from config import get_case_config
from data_loading.loaders import load_energy_load, load_openei_rate, load_solar_into_container
from model.core import build_model


def main() -> int:
    """Run a first end-to-end data loading path for the default case."""
    project_root = Path(__file__).resolve().parents[1]
    ### Case name specifies which case to run, this should match a folder in the config/cases directory
    case_name = os.getenv("DEROPT_CASE", "igiugig_multi_node")
    case_cfg = get_case_config(project_root, case_name)

# Loads the energy load from the case config (validates required fields internally).
    data = load_energy_load(case_cfg.energy_load)
    # Solar: fail hard if config points to a path that does not exist.
    if case_cfg.solar_path is not None:
        if not case_cfg.solar_path.exists():
            raise FileNotFoundError(
                f"Case config solar_path is set but file does not exist: {case_cfg.solar_path}. "
                "Check the path in your case builder or add the solar file."
            )
        load_solar_into_container(data, case_cfg.solar_path)
    solar_loaded = "solar_production_keys" in data.static

    # Technology/economic inputs are passed explicitly to the model builder,
    # separate from measured data in DataContainer.
    technology_parameters = case_cfg.technology_parameters or {} # These are user defined parameters for the technology that overrides the default parameters
    financials = asdict(case_cfg.financials) if case_cfg.financials is not None else {} # These are the financials for the technology and are used to annualize the capital costs

    # -------------------------------------------------------------------------
    # Utility rate: pulled in here from case config. When utility_rate_path is
    # set, we load the OpenEI JSON (utility-specific loader is chosen by the
    # "utility" field in the JSON) and pass the ParsedRate to the model. The
    # model does not use it yet; when a grid/utility block exists, it will
    # consume this for import cost and possibly demand charges.
    # Fail hard if config points to a rate file that does not exist.
    # -------------------------------------------------------------------------
    utility_rate = None
    if case_cfg.utility_rate_path is not None:
        if not case_cfg.utility_rate_path.exists():
            raise FileNotFoundError(
                f"Case config utility_rate_path is set but file does not exist: {case_cfg.utility_rate_path}. "
                "Check the path in your case builder or add the rate JSON file."
            )
        utility_rate = load_openei_rate(
            case_cfg.utility_rate_path,
            item_index=case_cfg.utility_rate_item_index,
        )

    # --- TEMPORARY: viewable DataFrames for debugging (inspect df_data, df_static in Variables) ---
    import pandas as pd
    _datetimes = data.timeseries.get("datetime")
    _ts = {
        k: v for k, v in data.timeseries.items()
        if k != "datetime" and isinstance(v, list) and len(v) == len(data.indices["time"])
    }
    df_data = pd.DataFrame(_ts)
    if _datetimes is not None:
        df_data.index = _datetimes
    df_data.index.name = "datetime"
    df_static = pd.Series(data.static)
    # Optional: write to CSV for viewing in Excel (set DEROPT_DEBUG_CSV=1 to enable)
    if os.environ.get("DEROPT_DEBUG_CSV"):
        _csv_path = project_root / "temp_debug_data.csv"
        df_data.to_csv(_csv_path)
        print(f"Debug: wrote {_csv_path} (open in Excel or any spreadsheet)")
    # --- end temporary ---

    model = build_model(
        data,
        technology_parameters=technology_parameters,
        financials=financials,
        utility_rate=utility_rate,
    )
    if model is None:
        raise RuntimeError(
            "build_model returned None. This should not happen when data is provided; "
            "check that data has required fields (electricity_load_keys, time, time_serial)."
        )

    # Slice 2/3 loader smoke output for quick local verification.
    time_count = len(data.indices["time"])
    first_serial = data.timeseries["time_serial"][0]
    first_load_key = data.static["electricity_load_keys"][0]
    first_kwh = data.timeseries[first_load_key][0]
    print(f"Case: {case_cfg.case_name}")
    print(f"Rows loaded: {time_count}")
    print(f"First time_serial: {first_serial:.6f}")
    print(f"First electricity load (kWh): {first_kwh:.5f} (key: {first_load_key})")
    print(f"Solar loaded: {solar_loaded}")
    print(f"Utility rate loaded: {utility_rate is not None}" + (f" ({utility_rate.rate_type}, {utility_rate.name[:40]}...)" if utility_rate else ""))
    has_solar_block = model is not None and hasattr(model, "solar_pv")
    print(f"Model built: {model is not None} (solar_pv block: {has_solar_block})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

