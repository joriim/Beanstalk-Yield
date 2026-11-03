"""Ingest adapters: vendor and ISO 11783 formats -> the common record schema.

Every adapter records the *entry level* of its records. Many vendor exports
already contain mass flow (L1B) or even delay-corrected positions (L2); the
chain must then skip those corrections so they are not applied twice
(design rule 2).

Adapters return a DataFrame with columns:
``t`` (s since epoch), ``machine_id``, ``x``, ``y`` (field CRS), ``mass_flow_wet``
(kg/s), ``moisture`` (%), ``swath`` (m), ``speed`` (m/s), ``heading`` (rad, may
be NaN), ``header_down`` (bool), ``gnss_quality`` (may be NaN), ``entry_level``.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from ..config import HarvestLog
from ..levels import Level
from .vendor_csv import read_vendor_csv


class Adapter(Protocol):
    def __call__(self, log: HarvestLog, crs: str) -> pd.DataFrame: ...


def _isoxml(log: HarvestLog, crs: str) -> pd.DataFrame:
    """ISOXML TaskData + TimeLog binaries (DDI 87 wet mass flow, 99 moisture)."""
    raise NotImplementedError(
        "ISOXML adapter is not implemented yet; export the log as CSV and use format: csv"
    )


ADAPTERS: dict[str, Adapter] = {"csv": read_vendor_csv, "isoxml": _isoxml}


def get_adapter(fmt: str) -> Adapter:
    try:
        return ADAPTERS[fmt]
    except KeyError as exc:
        raise ValueError(f"no ingest adapter for format {fmt!r}; have {sorted(ADAPTERS)}") from exc


def read_logs(logs: list[HarvestLog], crs: str) -> pd.DataFrame:
    """Read and concatenate all harvest logs, sorted by time."""
    frames = []
    for log in logs:
        if not log.path.exists():
            raise FileNotFoundError(f"harvest log not found: {log.path}")
        df = get_adapter(log.format)(log, crs)
        if Level.parse(df["entry_level"].iloc[0]) < Level.L1B:
            raise NotImplementedError("raw force (L0/L1A) logs need the L1 sensor module")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["t", "machine_id"], kind="stable", ignore_index=True)
