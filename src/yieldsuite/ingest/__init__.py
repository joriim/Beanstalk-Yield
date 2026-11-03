"""Ingest adapters: vendor and ISO 11783 formats -> the common record schema.

Every adapter records the *entry level* of its records. Many vendor exports
already contain mass flow (L1B) or even delay-corrected positions (L2); the
chain must then skip those corrections so they are not applied twice
(design rule 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from ..levels import Level


class Adapter(Protocol):
    format: str
    entry_level: Level

    def read(self, path: Path, machine_id: str) -> pd.DataFrame:
        """Return records with columns from ``yieldsuite.schema.RECORD_COLUMNS``."""
        ...


class IsoxmlAdapter:
    """ISOXML TaskData + TimeLog binaries (needs ``yieldsuite[ingest]``).

    DDI 87 = wet mass per time yield, DDI 99 = moisture, DDI 182 = dry mass per
    time yield at the reference moisture in DDI 184.
    """

    format = "isoxml"
    entry_level = Level.L1B

    def read(self, path: Path, machine_id: str) -> pd.DataFrame:
        raise NotImplementedError("ISOXML adapter is not implemented yet")


ADAPTERS: dict[str, Adapter] = {"isoxml": IsoxmlAdapter()}


def get_adapter(fmt: str) -> Adapter:
    try:
        return ADAPTERS[fmt]
    except KeyError as exc:
        raise ValueError(f"no ingest adapter for format {fmt!r}; have {sorted(ADAPTERS)}") from exc
