"""Technology module package for DERopt Python rebuild.

Registry: list of (config_key, register_function). Only technologies listed in
technology_parameters (with non-None value) are included in a run; omit a key
to exclude that technology. Add a new technology by adding its module and one
entry here.
"""

from technologies.solar_pv import register as register_solar_pv

REGISTRY = [
    ("solar_pv", register_solar_pv),
]
