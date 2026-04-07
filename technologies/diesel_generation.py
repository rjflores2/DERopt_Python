"""Backward-compatible import shim for diesel generator technology."""

from technologies.diesel_generator import add_diesel_generator_block, register

__all__ = [
    "add_diesel_generator_block",
    "register",
]
