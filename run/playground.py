"""Primary execution entry point for local case runs."""

import os
from pathlib import Path

from config import get_case_config
from data_loading.loaders import load_energy_load, load_solar_into_container
from model.core import build_model


def main() -> int:
    """Run a first end-to-end data loading path for the default case."""
    project_root = Path(__file__).resolve().parents[1]
    case_name = os.getenv("DEROPT_CASE", "igiugig_multi_node")
    case_cfg = get_case_config(project_root, case_name)

    data = load_energy_load(case_cfg.energy_load)
    if case_cfg.solar_path is not None and case_cfg.solar_path.exists():
        load_solar_into_container(data, case_cfg.solar_path)
        solar_loaded = "solar_production_keys" in data.static
    else:
        solar_loaded = False

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

    model = build_model()

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
    print(f"Model placeholder built: {model is None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

