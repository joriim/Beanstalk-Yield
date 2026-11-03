"""Product metadata, written as STAC item properties (pgSTAC in the server).

Each product records its level, parents, parameter hash and code version, so
any field-year can be reprocessed and compared (design rule 1).
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import __version__
from .levels import Level, ProductClass


def product_properties(
    *,
    level: Level,
    field_id: str,
    year: int,
    product_class: ProductClass,
    parents: list[str] | None = None,
    param_hash: str | None = None,
    qa: dict | None = None,
) -> dict:
    return {
        "yieldsuite:level": level.value,
        "yieldsuite:field_id": field_id,
        "yieldsuite:year": year,
        "yieldsuite:product_class": product_class.value,
        "yieldsuite:parents": parents or [],
        "yieldsuite:param_hash": param_hash,
        "yieldsuite:code_version": __version__,
        "yieldsuite:qa": qa or {},
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
