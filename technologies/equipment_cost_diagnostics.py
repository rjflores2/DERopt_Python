"""Shared helpers for technology equipment-cost / O&M diagnostics (warnings only).

Technology modules may call ``equipment_capital_om_warnings`` from ``collect_equipment_cost_diagnostics``
so capital vs O&M naming stays consistent across technologies.
"""

from __future__ import annotations

_FLOAT_TOL = 1e-9


def equipment_capital_om_warnings(
    scope: str,
    capital: float,
    om: float,
    *,
    capital_name: str,
    om_name: str,
) -> list[str]:
    """Return 0–2 warnings: negative capital or O&M, or both zero (suspicious debug / missing inputs)."""
    out: list[str] = []
    if capital < -_FLOAT_TOL or om < -_FLOAT_TOL:
        out.append(
            f"{scope}: negative cost parameter(s) ({capital_name}={capital:g}, {om_name}={om:g})."
        )
    elif abs(capital) <= _FLOAT_TOL and abs(om) <= _FLOAT_TOL:
        out.append(
            f"{scope}: {capital_name} and {om_name} are both zero (zero-cost equipment; may be intentional for debugging)."
        )
    return out
