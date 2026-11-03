"""L4 -> L5: fusion with satellite and drone imagery.

Implemented: Sentinel-2 features via openEO (``sentinel2``), yield-image model
(``model``), regression-kriging gap fill (``gapfill``), and ``fuse`` which
chains them.

Planned (see docs/design/processing-levels.md, "L4 -> L5"):

- ``residual_shift``: along-track shift maximising corr(Y(x), V(x + delta)).
- ``level_pass_stripes``: log Y_i = log f(V) + b_p + e, renormalised to the field total.
- ``zones``: multi-year relative yield + imagery -> KM-sPC zones.
- drone orthomosaic features (harmonised to Sentinel-2 reflectance).

Filled cells are always labelled and never reused as training data (design rule 6).
"""

from __future__ import annotations

from .features import feature_matrix
from .fuse import FusionResult, fuse
from .gapfill import gap_fill
from .model import YieldImageModel, fit_yield_image_model
from .sentinel2 import S2Request


def _todo(name: str):
    def fn(*args, **kwargs):
        raise NotImplementedError(f"L5 {name} is not implemented yet")

    fn.__name__ = name
    return fn


residual_shift = _todo("residual_shift")
level_pass_stripes = _todo("level_pass_stripes")
zones = _todo("zones")

__all__ = [
    "FusionResult",
    "S2Request",
    "YieldImageModel",
    "feature_matrix",
    "fit_yield_image_model",
    "fuse",
    "gap_fill",
    "level_pass_stripes",
    "residual_shift",
    "zones",
]
