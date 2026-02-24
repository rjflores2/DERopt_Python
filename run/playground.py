"""Primary execution entry point for local case runs."""

import os
from pathlib import Path

from config import get_case_config
from data_loading.loaders import load_energy_demand
from model.core import build_model


def main() -> int:
    """Run a first end-to-end data loading path for the default case."""
    project_root = Path(__file__).resolve().parents[1]
    case_name = os.getenv("DEROPT_CASE", "igiugig")
    case_cfg = get_case_config(project_root, case_name)

    data = load_energy_demand(case_cfg.energy_load)
    model = build_model()

    # Slice 2/3 loader smoke output for quick local verification.
    time_count = len(data.indices["time"])
    first_serial = data.timeseries["time_serial"][0]
    first_kw = data.timeseries["electricity_demand"][0]
    print(f"Case: {case_cfg.case_name}")
    print(f"Rows loaded: {time_count}")
    print(f"First time_serial: {first_serial:.6f}")
    print(f"First electricity_demand (kW): {first_kw:.5f}")
    print(f"Model placeholder built: {model is None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

