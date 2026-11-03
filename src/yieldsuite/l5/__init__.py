"""L4 -> L5: fusion with satellite and drone imagery.

Planned functions (see docs/design/processing-levels.md, "L4 -> L5"):

- ``fit_yield_image_model``: Y_g = f(V_g) + r_g on clean, well-covered cells
  (linear or random forest), per field-year only.
- ``residual_shift``: along-track shift maximising corr(Y(x), V(x + delta)).
- ``level_pass_stripes``: log Y_i = log f(V) + b_p + e, renormalised to the field total.
- ``gap_fill``: regression kriging for low-coverage cells, with per-cell variance.
- ``zones``: multi-year relative yield + imagery -> KM-sPC zones.

Filled cells are always labelled and never reused as training data (design rule 6).
"""

from __future__ import annotations


def _todo(name: str):
    def fn(*args, **kwargs):
        raise NotImplementedError(f"L5 {name} is not implemented yet")

    fn.__name__ = name
    return fn


fit_yield_image_model = _todo("fit_yield_image_model")
residual_shift = _todo("residual_shift")
level_pass_stripes = _todo("level_pass_stripes")
gap_fill = _todo("gap_fill")
zones = _todo("zones")
