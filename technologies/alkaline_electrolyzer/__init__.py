"""Alkaline electrolyzer technology package."""

from .block import add_alkaline_electrolyzer_block


def register(
    model,
    data,
    *,
    technology_parameters=None,
    financials=None,
):
    """
    Registry hook: build the alkaline electrolyzer block.

    - ``technology_parameters["alkaline_electrolyzer"]`` -> dict passed as ``alkaline_electrolyzer_params``.
    """
    alkaline_electrolyzer_params = (technology_parameters or {}).get("alkaline_electrolyzer") or {}
    return add_alkaline_electrolyzer_block(
        model,
        data,
        alkaline_electrolyzer_params=alkaline_electrolyzer_params,
        financials=financials or {},
    )


__all__ = [
    "add_alkaline_electrolyzer_block",
    "register",
]
