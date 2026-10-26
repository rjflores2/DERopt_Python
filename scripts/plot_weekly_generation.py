"""Plot a stacked-area ("fill") chart of hourly generation by source for one week.

Reads the per-technology CSV written by ``utilities.results.write_timeseries_csv`` -- set
``DEROPT_EXPORT_CSV=1`` when running ``run/playground.py`` to produce
``results_timeseries.csv`` in the project root first.

Layer order (bottom to top): diesel, hydrokinetic, solar PV, PEM fuel cell,
battery discharge -- diesel on the bottom, solar/hydrokinetic in the middle, battery
discharge and fuel cell on top. Load is drawn as a black dashed reference line, not stacked.

Usage::

    python scripts/plot_weekly_generation.py
    python scripts/plot_weekly_generation.py --start-date 2022-07-04
    python scripts/plot_weekly_generation.py --start-index 4000 --hours 168
    python scripts/plot_weekly_generation.py --csv results_timeseries.csv --out demo_week.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# (csv column, legend label, color) -- order here is the stacking order, bottom to top.
LAYER_ORDER = [
    ("diesel_generator_kwh", "Diesel", "#7f7f7f"),
    ("hydrokinetic_kwh", "Hydrokinetic", "#1f77b4"),
    ("solar_pv_kwh", "Solar PV", "#ff7f0e"),
    ("pem_fuel_cell_kwh", "PEM Fuel Cell", "#9467bd"),
    ("battery_energy_storage_kwh", "Battery discharge", "#2ca02c"),
]


def plot_weekly_generation(
    csv_path: Path | str,
    out_path: Path | str,
    *,
    start_date: str | None = None,
    start_index: int = 0,
    hours: int = 168,
    show: bool = False,
) -> Path:
    """Build the stacked-area generation chart and save it to ``out_path``.

    Shared by the CLI entry point below and ``run/playground.py`` (called automatically
    right after ``write_timeseries_csv`` when ``DEROPT_EXPORT_CSV`` is set, so a single
    "press play" produces the chart with no separate manual step). Imports matplotlib
    lazily so importing this module (e.g. from playground.py) doesn't pull in a plotting
    backend unless a chart is actually requested.
    """
    import matplotlib.pyplot as plt

    csv_path = Path(csv_path)
    out_path = Path(out_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found. Run run/playground.py with DEROPT_EXPORT_CSV=1 first "
            "to produce it."
        )

    df = pd.read_csv(csv_path, parse_dates=["datetime"])

    if start_date:
        start = pd.Timestamp(start_date)
        end = start + pd.Timedelta(hours=hours)
        window = df[(df["datetime"] >= start) & (df["datetime"] < end)]
    else:
        window = df.iloc[start_index : start_index + hours]

    if window.empty:
        raise ValueError(
            "Selected window has no rows -- check start_date/start_index against the "
            f"CSV's date range ({df['datetime'].min()} to {df['datetime'].max()})."
        )

    layers = [(col, label, color) for col, label, color in LAYER_ORDER if col in window.columns]
    if not layers:
        raise ValueError(
            f"None of the expected generation columns {[c for c, _, _ in LAYER_ORDER]} are in "
            f"{csv_path}; available columns: {list(window.columns)}"
        )

    fig, ax = plt.subplots(figsize=(12, 6))
    x = window["datetime"]
    series = [window[col].to_numpy() for col, _, _ in layers]
    labels = [label for _, label, _ in layers]
    colors = [color for _, _, color in layers]
    ax.stackplot(x, series, labels=labels, colors=colors, alpha=0.85)

    if "load_kwh" in window.columns:
        ax.plot(x, window["load_kwh"], color="black", linewidth=1.5, linestyle="--", label="Load")

    ax.set_ylabel("kWh per hour")
    ax.set_title(f"Hourly generation by source ({x.iloc[0]:%b %d} – {x.iloc[-1]:%b %d})")
    ax.legend(loc="upper left", ncol=3, fontsize=9)
    ax.margins(x=0)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    if show:
        try:
            import matplotlib

            if matplotlib.get_backend().lower() != "agg":
                plt.show()
        except Exception:
            pass
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default="results_timeseries.csv", type=Path)
    parser.add_argument(
        "--start-date", default=None, help="e.g. 2022-07-04; plots the week starting here"
    )
    parser.add_argument(
        "--start-index", type=int, default=0,
        help="row index to start from if --start-date is not given (default: 0, start of file)",
    )
    parser.add_argument("--hours", type=int, default=168, help="window length in hours (default: 1 week)")
    parser.add_argument("--out", default="weekly_generation.png", type=Path)
    args = parser.parse_args()

    out = plot_weekly_generation(
        args.csv,
        args.out,
        start_date=args.start_date,
        start_index=args.start_index,
        hours=args.hours,
        show=True,
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
