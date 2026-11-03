"""Load reconciliation: scale the monitor to weighed mass, per machine.

Uses all harvested mass, including flagged records, because flagged records
still carried real grain to the tank.
"""

from __future__ import annotations

import numpy as np


def reconciliation_factor(scale_masses_kg, monitor_masses_kg) -> float:
    """k = sum(scale) / sum(monitor) over the loads linked to one machine."""
    scale = float(np.sum(scale_masses_kg))
    monitor = float(np.sum(monitor_masses_kg))
    if monitor <= 0:
        raise ValueError("monitor mass must be positive")
    return scale / monitor


def apply_reconciliation(values, machine_ids, factors: dict[str, float]):
    """Multiply yield or mass by the factor of the machine that produced it."""
    values = np.asarray(values, dtype=float)
    ids = np.asarray(machine_ids)
    missing = set(np.unique(ids)) - set(factors)
    if missing:
        raise KeyError(f"no reconciliation factor for machines: {sorted(missing)}")
    k = np.array([factors[m] for m in ids], dtype=float)
    return values * k
