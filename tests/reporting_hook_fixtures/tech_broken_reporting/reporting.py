"""Simulates a reporting module that fails while loading (e.g. bad sub-import)."""

raise ImportError("broken_internal_import")
