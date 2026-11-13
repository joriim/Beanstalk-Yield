"""Lateral antenna offset from the pass geometry itself.

If the logged position sits ``e`` metres to the right of the true header centre
(antenna mounted off-centre and not entered in the machine profile), every pass
is drawn ``e`` metres to the right of where it was cut. Two adjacent passes
driven in opposite directions then appear

- ``w - 2e`` apart when each lies on the other's right ("facing" pair), and
- ``w + 2e`` apart when each lies on the other's left ("back-to-back" pair),

where ``w`` is the true spacing. In back-and-forth harvesting the two kinds
alternate, so the common part (header width, the driver's habitual overlap)
cancels and

    e = (median spacing back-to-back - median spacing facing) / 4.

The correction to the profile is ``right_new = right_current - e``. The forward
offset cannot be separated from the flow delay this way (both shift the pattern
along the track); measure it with a tape and enter it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .delay import opposite_pass_pairs


@dataclass
class OffsetEstimate:
    lateral_error_m: float | None  # e: logged position right of the header centre
    identifiable: bool
    n_facing: int
    n_back_to_back: int
    spacing_facing_m: float | None = None
    spacing_back_to_back_m: float | None = None
    ci95_m: tuple[float, float] | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        if self.ci95_m is not None:
            d["ci95_m"] = list(self.ci95_m)
        return d


def estimate_lateral_offset(
    x,
    y,
    heading,
    pass_id,
    swath,
    min_pairs: int = 100,
    n_boot: int = 200,
    seed: int = 0,
) -> OffsetEstimate:
    """Estimate the uncorrected lateral offset ``e`` (m, positive = to the right).

    ``x``/``y`` are header positions with the current profile offset applied and
    ``heading`` is in radians clockwise from north (as in L2).
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    h = np.asarray(heading, float)
    i, j = opposite_pass_pairs(x, y, h, pass_id, swath, lateral_range=(0.4, 1.6))
    if i.size == 0:
        return OffsetEstimate(None, False, 0, 0, reason="no opposite-pass pairs")
    dx, dy = x[j] - x[i], y[j] - y[i]
    # right unit vector of record i: (cos h, -sin h) for heading clockwise from north
    side = dx * np.cos(h[i]) - dy * np.sin(h[i])
    spacing = np.abs(side)
    facing = side > 0  # j on i's right; for opposite headings i is then on j's right too
    a, b = spacing[facing], spacing[~facing]
    if a.size < min_pairs or b.size < min_pairs:
        return OffsetEstimate(
            None,
            False,
            int(a.size),
            int(b.size),
            reason=(
                f"too few pairs of each kind ({a.size} facing, {b.size} back-to-back; "
                f"need {min_pairs}); the field may be harvested in lands, not back and forth"
            ),
        )
    e = (np.median(b) - np.median(a)) / 4.0
    rng = np.random.default_rng(seed)
    boot = [
        (np.median(rng.choice(b, b.size)) - np.median(rng.choice(a, a.size))) / 4.0
        for _ in range(n_boot)
    ]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return OffsetEstimate(
        float(e),
        True,
        int(a.size),
        int(b.size),
        float(np.median(a)),
        float(np.median(b)),
        (float(lo), float(hi)),
    )
