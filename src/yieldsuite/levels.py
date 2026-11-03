"""Processing levels and product classes.

Levels follow the EOSDIS logic described in docs/design/processing-levels.md.
"""

from __future__ import annotations

from enum import Enum


class Level(str, Enum):
    """Processing level of a record or product."""

    L0 = "L0"  # raw log as received
    L1A = "L1A"  # decoded, SI units, one clock
    L1B = "L1B"  # mass flow, moisture-standardised, not yet located
    L2 = "L2"  # georeferenced record polygons with yield
    L3 = "L3"  # reconciled to weighed loads, flagged
    L4 = "L4"  # gridded, mass-conserving raster
    L5 = "L5"  # fused with imagery, corrected, zones

    @property
    def order(self) -> int:
        return _ORDER.index(self)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Level):
            return NotImplemented
        return self.order < other.order

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Level):
            return NotImplemented
        return self.order <= other.order

    @classmethod
    def parse(cls, value: str | Level) -> Level:
        if isinstance(value, Level):
            return value
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            valid = ", ".join(lv.value for lv in cls)
            raise ValueError(f"unknown level {value!r}; expected one of {valid}") from exc

    @classmethod
    def range(cls, start: Level, stop: Level) -> list[Level]:
        """Levels from ``start`` to ``stop`` inclusive."""
        return [lv for lv in _ORDER if start.order <= lv.order <= stop.order]


_ORDER = list(Level)


class ProductClass(str, Enum):
    """What a map can honestly claim, given the data that went into it."""

    ABSOLUTE = "absolute"  # t/ha, reconciled to scale weights
    ABSOLUTE_UNRECONCILED = "absolute_unreconciled"  # t/ha at monitor calibration
    RELATIVE = "relative"  # index or zones, no trustworthy absolute level


def classify_product(
    *,
    has_harvester: bool,
    has_scale_weights: bool = False,
    has_imagery: bool = False,
) -> ProductClass:
    """Best product class a field-year's data stack supports.

    | Inputs                                   | Class                  |
    |------------------------------------------|------------------------|
    | harvester + scale weights (+ imagery)    | absolute               |
    | harvester only                           | absolute_unreconciled  |
    | imagery only (other years' harvester ok) | relative               |
    """
    if has_harvester:
        if has_scale_weights:
            return ProductClass.ABSOLUTE
        return ProductClass.ABSOLUTE_UNRECONCILED
    if has_imagery:
        return ProductClass.RELATIVE
    raise ValueError("no harvester data and no imagery: nothing to map")
