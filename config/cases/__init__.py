"""Case builders: one module per case, auto-discovered by get_case_config.

Add a new case by creating config/cases/<name>.py with:
    def default_<name>_case(project_root: Path) -> CaseConfig: ...
No need to edit this __init__.py.
"""

import importlib
import pkgutil


def _discover_case_builders() -> dict[str, object]:
    """Find all default_*_case functions in submodules."""
    builders: dict[str, object] = {}
    for importer, modname, ispkg in pkgutil.iter_modules(__path__, __name__ + "."):
        try:
            mod = importlib.import_module(modname)
            for name in dir(mod):
                if name.startswith("default_") and name.endswith("_case"):
                    obj = getattr(mod, name)
                    if callable(obj):
                        builders[name] = obj
        except Exception:
            pass
    return builders


_builders = _discover_case_builders()
globals().update(_builders)
__all__ = sorted(_builders.keys())
