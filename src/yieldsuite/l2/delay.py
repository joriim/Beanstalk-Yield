"""Flow delay estimation.

The data-driven estimator in the design doc picks the delay that minimises the
yield discrepancy between adjacent passes driven in opposite directions:

    tau_hat = argmin_tau sum_{(i,j) in N} (Y_i(tau) - Y_j(tau))^2

This module provides the search; building the opposite-pass neighbour pairs is
TODO (see issue list) and is passed in as ``discrepancy``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import numpy as np


def estimate_delay(
    candidates_s: Iterable[float],
    discrepancy: Callable[[float], float],
) -> tuple[float, np.ndarray]:
    """Grid search for the delay minimising ``discrepancy(tau)``.

    Returns the best delay and the objective value for every candidate, so the
    curve can be stored in the QA report (a flat curve means the delay is not
    identifiable from this field).
    """
    taus = np.asarray(list(candidates_s), dtype=float)
    if taus.size == 0:
        raise ValueError("no candidate delays")
    values = np.array([discrepancy(float(t)) for t in taus])
    return float(taus[int(np.nanargmin(values))]), values
