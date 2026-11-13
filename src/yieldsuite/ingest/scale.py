"""Scale tickets: weighed loads for reconciliation.

CSV columns: ``mass_kg`` (required), ``machine_id`` (optional; without it one
factor is used for the field), ``moisture_pct`` (optional; converts the weight
to the reference moisture), ``field_share`` (optional, 0-1: share of a load
that came from this field when one load holds grain from several fields).
Other columns (ticket_ref, date, note, ...) are kept for the record only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..l1.moisture import to_reference_moisture


def read_scale_tickets(path: str | Path, reference_pct: float) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "mass_kg" not in df.columns:
        raise ValueError(f"{path}: needs a mass_kg column")
    mass = df["mass_kg"].to_numpy(dtype=float)
    if "moisture_pct" in df.columns:
        mc = df["moisture_pct"].to_numpy(dtype=float)
        mass = np.where(np.isfinite(mc), to_reference_moisture(mass, mc, reference_pct), mass)
    if "field_share" in df.columns:
        share = pd.to_numeric(df["field_share"], errors="coerce").to_numpy(dtype=float)
        mass = mass * np.where(np.isfinite(share), share, 1.0)
    if "machine_id" in df.columns:
        machine = df["machine_id"].astype("string").str.strip().fillna("*").replace("", "*")
        machine = machine.replace({"nan": "*", "None": "*"}).astype(str)
    else:
        machine = "*"
    return pd.DataFrame({"machine_id": machine, "mass_ref_kg": mass})
