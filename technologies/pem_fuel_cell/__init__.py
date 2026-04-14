"""PEM fuel cell technology package."""

from .block import add_pem_fuel_cell_block


def register(
    model,
    data,
    *,
    technology_parameters=None,
    financials=None,
):
    """
    Registry hook: build the PEM fuel cell block.

    - ``technology_parameters["pem_fuel_cell"]`` -> dict passed as ``pem_fuel_cell_params``.
    """
    pem_fuel_cell_params = (technology_parameters or {}).get("pem_fuel_cell") or {}
    return add_pem_fuel_cell_block(
        model,
        data,
        pem_fuel_cell_params=pem_fuel_cell_params,
        financials=financials or {},
    )


__all__ = [
    "add_pem_fuel_cell_block",
    "register",
]
