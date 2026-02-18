"""Reusable financial helper functions.

Keep generic math here so technology/utility modules can share
consistent amortization and discounting behavior.
"""


def capital_recovery_factor(discount_rate: float, lifetime_years: float) -> float:
    """Return CRF for annualizing one-time capital costs.

    Args:
        discount_rate: Real discount rate (e.g. 0.07 for 7%).
        lifetime_years: Asset lifetime in years.

    Returns:
        Capital recovery factor (unitless).
    """
    if lifetime_years <= 0:
        raise ValueError("lifetime_years must be > 0")
    if discount_rate < 0:
        raise ValueError("discount_rate must be >= 0")

    if discount_rate == 0:
        return 1.0 / lifetime_years

    r = discount_rate
    n = lifetime_years
    return (r * (1.0 + r) ** n) / (((1.0 + r) ** n) - 1.0)


def annualized_capex(capex_total: float, discount_rate: float, lifetime_years: float) -> float:
    """Convert one-time capex to equivalent annualized cost.

    Args:
        capex_total: Total up-front capital + installation cost in dollars.
        discount_rate: Real discount rate (e.g. 0.07 for 7%).
        lifetime_years: Asset lifetime in years.

    Returns:
        Annualized cost in dollars/year.
    """
    if capex_total < 0:
        raise ValueError("capex_total must be >= 0")
    return capex_total * capital_recovery_factor(discount_rate, lifetime_years)

