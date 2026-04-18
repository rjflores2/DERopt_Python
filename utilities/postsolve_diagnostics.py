"""Post-solve diagnostics: flag suspicious patterns in the optimal solution.

These checks run *after* the solver returns and operate on variable values, not the
model structure. They surface signals (e.g. simultaneous charge + discharge in a
storage device) that usually indicate a modeling issue elsewhere (e.g. zero-cost
surplus region, misspecified efficiencies) but may also be immaterial to the
overall result. Reports are non-fatal — the caller decides what to do with them.
"""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo

# Storage blocks expose a (charge, discharge) flow variable pair. The names differ
# by device physics (electricity storage uses ``charge_power``/``discharge_power``;
# hydrogen storage uses ``hydrogen_charge_flow``/``hydrogen_discharge_flow``). Each
# pair is checked with the same logic below.
_STORAGE_FLOW_VAR_PAIRS: list[tuple[str, str, str]] = [
    # (charge_var_name, discharge_var_name, unit_label)
    ("charge_power", "discharge_power", "kWh"),
    ("hydrogen_charge_flow", "hydrogen_discharge_flow", "kWh-H2_LHV"),
]

# Default simultaneity threshold: values below this are treated as numerical noise.
# 1e-6 kWh over a year-long horizon aggregates to at most ~9 mWh — well below any
# physically meaningful rounding.
_DEFAULT_TOLERANCE_KWH: float = 1e-6

# Keep top-offender lists bounded so report sizes stay reasonable.
_MAX_TOP_OFFENDERS: int = 5


def check_simultaneous_charge_discharge(
    model: pyo.ConcreteModel,
    *,
    tolerance: float = _DEFAULT_TOLERANCE_KWH,
    max_offenders: int = _MAX_TOP_OFFENDERS,
) -> dict[str, Any]:
    """Scan every storage block in ``model`` for timesteps with simultaneous charge + discharge.

    Simultaneous operation is physically possible during slow transitions and is
    often mathematically optimal in zero-marginal-cost regions (round-trip losses
    are the only natural deterrent). This function does not judge whether the
    behavior is *wrong* — it only surfaces where and by how much it occurs so
    the user can decide whether it's material.

    Returns:
        Dict keyed by block name. Each entry contains:
            simultaneous_timesteps: number of (node, t) pairs with both flows > tolerance
            total_timesteps: len(NODES) * len(T) for that block (denominator reference)
            simultaneous_throughput: sum of min(charge, discharge) across all flagged
                (node, t) pairs — interpretable as "energy burned by round-tripping"
            total_charge / total_discharge: horizon totals for context
            fraction_of_charge: simultaneous_throughput / total_charge (0 if total_charge == 0)
            top_offenders: up to ``max_offenders`` worst (node, t) pairs by simultaneous flow
            units: unit label for the throughput metrics ("kWh" or "kWh-H2_LHV")
        Blocks without matching flow var pairs are omitted.
    """
    report: dict[str, Any] = {}
    nodes = list(model.NODES)
    time_set = list(model.T)

    for blk in model.component_objects(pyo.Block, descend_into=False):
        for charge_name, discharge_name, unit_label in _STORAGE_FLOW_VAR_PAIRS:
            charge_var = getattr(blk, charge_name, None)
            discharge_var = getattr(blk, discharge_name, None)
            if charge_var is None or discharge_var is None:
                continue

            block_report = _scan_block(
                charge_var=charge_var,
                discharge_var=discharge_var,
                nodes=nodes,
                time_set=time_set,
                tolerance=tolerance,
                max_offenders=max_offenders,
                unit_label=unit_label,
            )
            if block_report is not None:
                report[str(blk.name)] = block_report
            break

    return report


def _scan_block(
    *,
    charge_var: pyo.Var,
    discharge_var: pyo.Var,
    nodes: list,
    time_set: list,
    tolerance: float,
    max_offenders: int,
    unit_label: str,
) -> dict[str, Any] | None:
    """Evaluate charge/discharge vars at every (node, t) and compile the block-level report.

    Returns ``None`` if no variable values are available (e.g. model not yet solved).
    """
    offenders: list[tuple[float, str, Any, float, float]] = []  # (simultaneous, n, t, c, d)
    simultaneous_total = 0.0
    total_charge = 0.0
    total_discharge = 0.0
    simultaneous_count = 0

    for n in nodes:
        for t in time_set:
            c_val = pyo.value(charge_var[n, t], exception=False)
            d_val = pyo.value(discharge_var[n, t], exception=False)
            # A variable with no value (unsolved / uninitialized) returns None; skip
            # rather than crash — the check is best-effort.
            if c_val is None or d_val is None:
                return None
            c = float(c_val)
            d = float(d_val)
            total_charge += c
            total_discharge += d
            # Simultaneous only when *both* exceed tolerance; single-direction
            # flow is the normal operating mode and must not trip the flag.
            if c > tolerance and d > tolerance:
                simultaneous = min(c, d)
                simultaneous_total += simultaneous
                simultaneous_count += 1
                offenders.append((simultaneous, n, t, c, d))

    if simultaneous_count == 0:
        return {
            "simultaneous_timesteps": 0,
            "total_timesteps": len(nodes) * len(time_set),
            "simultaneous_throughput": 0.0,
            "total_charge": total_charge,
            "total_discharge": total_discharge,
            "fraction_of_charge": 0.0,
            "top_offenders": [],
            "units": unit_label,
        }

    offenders.sort(key=lambda row: row[0], reverse=True)
    top = [
        {"node": str(n), "t": t, "charge": c, "discharge": d, "simultaneous": s}
        for (s, n, t, c, d) in offenders[:max_offenders]
    ]

    return {
        "simultaneous_timesteps": simultaneous_count,
        "total_timesteps": len(nodes) * len(time_set),
        "simultaneous_throughput": simultaneous_total,
        "total_charge": total_charge,
        "total_discharge": total_discharge,
        "fraction_of_charge": (
            simultaneous_total / total_charge if total_charge > 0 else 0.0
        ),
        "top_offenders": top,
        "units": unit_label,
    }


def format_simultaneous_charge_discharge_warnings(report: dict[str, Any]) -> list[str]:
    """Render the report as a list of human-readable warning lines.

    Returns an empty list when no block has simultaneous operation. Callers can
    inspect ``report`` directly for structured data; this is purely for CLI output.
    """
    lines: list[str] = []
    for block_name, entry in report.items():
        count = entry.get("simultaneous_timesteps", 0)
        if count <= 0:
            continue
        fraction = float(entry.get("fraction_of_charge", 0.0))
        throughput = float(entry.get("simultaneous_throughput", 0.0))
        total_charge = float(entry.get("total_charge", 0.0))
        units = entry.get("units", "kWh")
        # Label severity by fraction of total charging that is "wasted" to
        # simultaneous operation; < 0.1% is almost always numerical, 0.1%–1% is
        # worth a look, >1% probably reflects a modeling gap (e.g. zero-cost region).
        if fraction < 1e-3:
            severity = "note"
        elif fraction < 1e-2:
            severity = "warning"
        else:
            severity = "ALERT"
        lines.append(
            f"  [{severity}] {block_name}: simultaneous charge+discharge at "
            f"{count} timesteps ({throughput:,.3f} {units} 'burned' out of "
            f"{total_charge:,.1f} total charged = {fraction * 100:.3f}%)"
        )
        for off in entry.get("top_offenders", []):
            lines.append(
                f"      node={off['node']} t={off['t']}: "
                f"charge={off['charge']:.3f} {units}, discharge={off['discharge']:.3f} {units}"
            )
        if severity == "note":
            lines.append(
                "      -> Below 0.1% of charging; likely numerical tolerance, safe to ignore."
            )
        elif severity == "warning":
            lines.append(
                "      -> Worth a look. Common causes: zero-cost import/export windows, "
                "efficiencies set to 1.0, missing curtailment cost."
            )
        else:
            lines.append(
                "      -> Material. Inspect import/export prices, efficiencies, and "
                "any zero-marginal-cost generation at the flagged timesteps."
            )
    return lines
