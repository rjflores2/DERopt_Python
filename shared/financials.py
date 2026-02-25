"""Reusable financial helper functions.

Technology and utility modules use these for consistent amortization.
Capital is split into debt and equity with separate payback periods and rates.
Financial parameters (debt fraction, years, rates) are intended to be
user-configurable via config (e.g. CaseConfig.financials).
"""


def capital_recovery_factor(discount_rate: float, lifetime_years: float) -> float:
    """Return CRF for annualizing one-time capital costs.

    CRF = r * (1+r)^n / ((1+r)^n - 1). Annual payment = principal * CRF.

    Args:
        discount_rate: Interest/discount rate (e.g. 0.08 for 8%).
        lifetime_years: Amortization period in years.

    Returns:
        Unitless factor.
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
    """Convert one-time capex to equivalent annualized cost (single rate).

    Args:
        capex_total: Total up-front capital in dollars.
        discount_rate: Real discount rate (e.g. 0.07 for 7%).
        lifetime_years: Asset lifetime in years.

    Returns:
        Annualized cost in dollars/year.
    """
    if capex_total < 0:
        raise ValueError("capex_total must be >= 0")
    return capex_total * capital_recovery_factor(discount_rate, lifetime_years)


def annualization_factor_debt_equity(
    debt_fraction: float = 0.5,
    debt_years: float = 10.0,
    debt_rate: float = 0.08,
    equity_years: float = 5.0,
    equity_rate: float = 0.15,
    levelization_years: float | None = None,
    **kwargs: float,
) -> float:
    """Convert one-time capital into an equivalent levelized annual payment (debt + equity).

    Debt and equity are amortized separately with their own payback periods and rates.
    We compute the actual total nominal payments over time, then levelize to a constant
    annual payment over levelization_years so that total payments match. This avoids
    overstating cost: simply adding debt_frac*CRF_d + equity_frac*CRF_e would imply
    paying both annuities every year and would overestimate when equity payback is
    shorter than debt.

    Steps:
    1. Debt annual payment = (capital * debt_fraction) * CRF(debt_rate, debt_years).
       Equity annual payment = (capital * equity_fraction) * CRF(equity_rate, equity_years).
    2. Total nominal payments = debt_annual * debt_years + equity_annual * equity_years.
    3. Equivalent constant annual = total_nominal / levelization_years.
    4. Factor = equivalent_annual / capital (so annual_cost = capital * factor).

    All parameters can be overridden via config (e.g. from CaseConfig.financials or
    data.static["financials"]). Pass as keyword arguments or as a dict with **.

    Args:
        debt_fraction: Fraction of capital financed by debt (0..1); equity = 1 - debt_fraction.
        debt_years: Debt payback period in years.
        debt_rate: Debt interest rate (e.g. 0.08 for 8%).
        equity_years: Equity payback period in years.
        equity_rate: Equity return rate (e.g. 0.15 for 15%).
        levelization_years: Years over which to levelize. Default: max(debt_years, equity_years).
        **kwargs: Ignored (allows passing config dict with extra keys).

    Returns:
        Unitless factor; annual_payment = capital * factor.
    """
    if not 0 <= debt_fraction <= 1:
        raise ValueError("debt_fraction must be between 0 and 1")
    equity_fraction = 1.0 - debt_fraction

    crf_debt = capital_recovery_factor(debt_rate, debt_years)
    crf_equity = capital_recovery_factor(equity_rate, equity_years)

    # Total nominal $ paid over time (per $ of capital)
    total_nominal_per_dollar = (
        debt_fraction * crf_debt * debt_years + equity_fraction * crf_equity * equity_years
    )

    L = levelization_years if levelization_years is not None else max(debt_years, equity_years)
    if L <= 0:
        raise ValueError("levelization_years must be > 0")

    # Equivalent constant annual payment per $ of capital
    factor = total_nominal_per_dollar / L
    return factor
