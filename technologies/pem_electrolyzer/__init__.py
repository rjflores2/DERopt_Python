"""PEM electrolyzer technology package."""

from .block import add_pem_electrolyzer_block


def register(
    model,
    data,
    *,
    technology_parameters=None,
    financials=None,
):
    """
    Registry hook: build the PEM electrolyzer block.

    - ``technology_parameters["pem_electrolyzer"]`` -> dict passed as ``pem_electrolyzer_params``.
    """
    pem_electrolyzer_params = (technology_parameters or {}).get("pem_electrolyzer") or {}
    return add_pem_electrolyzer_block(
        model,
        data,
        pem_electrolyzer_params=pem_electrolyzer_params,
        financials=financials or {},
    )


__all__ = [
    "add_pem_electrolyzer_block",
    "register",
]
