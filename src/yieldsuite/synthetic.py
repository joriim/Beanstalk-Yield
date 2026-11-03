"""Synthetic field-year with a known true yield, for tests and ``yieldsuite demo``.

A combine with a 6 m header drives north-south passes over a 240 x 160 m field
in EPSG:3067. The logged mass flow is delayed by tau_d, the monitor reads 7 %
high, and grain is logged at 18 % moisture. Scale tickets carry the true mass,
so the chain must recover the true yield pattern and level.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

X0, Y0 = 400_000.0, 6_950_000.0
FIELD_W, FIELD_H = 240.0, 160.0
HEADER_W = 6.0
SPEED = 1.5  # m/s
TAU_D = 12.0  # s
ANTENNA_BEHIND_HEADER = 3.2  # m
MOISTURE = 18.0
MONITOR_GAIN = 1.07
REF_MOISTURE = 14.0


def true_yield(x, y):
    """t/ha at 14 % moisture."""
    u = (np.asarray(x) - X0) / 200.0
    v = (np.asarray(y) - Y0) / 150.0
    return 7.0 + 1.5 * np.sin(2 * np.pi * u) + 1.0 * np.cos(2 * np.pi * v)


@dataclass
class SyntheticField:
    log: pd.DataFrame
    tickets: pd.DataFrame
    true_mass_ref_kg: float


def make_field(seed: int = 0, noise: float = 0.03, n_spikes: int = 15) -> SyntheticField:
    rng = np.random.default_rng(seed)
    n_pass = int(FIELD_W // HEADER_W)
    rows = []
    t = 0.0
    true_mass = 0.0
    for p in range(n_pass):
        x = X0 + HEADER_W * (p + 0.5)
        north = p % 2 == 0
        heading = 0.0 if north else 180.0
        sign = 1.0 if north else -1.0
        y_start = Y0 if north else Y0 + FIELD_H
        n_cut = int(FIELD_H // SPEED)  # stay inside the field
        n_tail = int(TAU_D) + 4  # keep driving with header up while the elevator empties
        cut_hist = []  # wet mass flow cut each second, at monitor gain
        for k in range(n_cut + n_tail):
            hy = y_start + sign * SPEED * (k + 1)  # header centre at end of second k
            cutting = k < n_cut
            if cutting:
                y_mid = hy - sign * SPEED / 2
                ref_kg_s = true_yield(x, y_mid) / 10.0 * SPEED * HEADER_W
                true_mass += ref_kg_s
                wet = ref_kg_s * (100 - REF_MOISTURE) / (100 - MOISTURE)
            else:
                wet = 0.0
            cut_hist.append(wet)
            delayed = cut_hist[k - int(TAU_D)] if k >= TAU_D else 0.0
            logged = delayed * MONITOR_GAIN * (1 + rng.normal(0, noise))
            rows.append(
                {
                    "Time": t + k,
                    "Easting": x,
                    "Northing": hy - sign * ANTENNA_BEHIND_HEADER,
                    "Mass_Flow_kg_s": max(logged, 0.0),
                    "Moisture_pct": MOISTURE + rng.normal(0, 0.2),
                    "Swath_m": HEADER_W if cutting else 0.0,
                    "Speed_kmh": SPEED * 3.6,
                    "Heading_deg": heading,
                    "Header": 1 if cutting else 0,
                    "GPS_Fix": 4,
                }
            )
        t += n_cut + n_tail + 20  # headland turn, logging paused
    log = pd.DataFrame(rows)
    spikes = rng.choice(np.flatnonzero(log["Header"] == 1), n_spikes, replace=False)
    log.loc[spikes, "Mass_Flow_kg_s"] *= 4.0
    log["Time"] = (pd.Timestamp("2026-08-10T10:00:00Z") + pd.to_timedelta(log["Time"], "s")).map(
        lambda ts: ts.isoformat()
    )
    wet_total = true_mass * (100 - REF_MOISTURE) / (100 - MOISTURE)
    n_loads = 6
    tickets = pd.DataFrame(
        {
            "machine_id": "combine-a",
            "mass_kg": np.full(n_loads, wet_total / n_loads),
            "moisture_pct": MOISTURE,
        }
    )
    return SyntheticField(log, tickets, true_mass)


def write_demo(out_dir: str | Path, seed: int = 0) -> Path:
    """Write log, scale tickets and a field-year config; return the config path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = make_field(seed)
    f.log.to_csv(out / "combine-a_2026.csv", index=False)
    f.tickets.to_csv(out / "scale_tickets.csv", index=False)
    config = {
        "field_id": "synthetic-01",
        "year": 2026,
        "crs": "EPSG:3067",
        "reference_moisture_pct": REF_MOISTURE,
        "grid": {"resolution_m": 20, "origin": [0, 0]},
        "machines": [
            {
                "machine_id": "combine-a",
                "header_width_m": HEADER_W,
                "tau_d_s": TAU_D,
                "tau_m_s": TAU_D,
                "antenna_offset_m": [ANTENNA_BEHIND_HEADER, 0.0],
            }
        ],
        "harvest_logs": [
            {
                "path": "combine-a_2026.csv",
                "machine_id": "combine-a",
                "format": "csv",
                "columns": {
                    "time": "Time",
                    "x": "Easting",
                    "y": "Northing",
                    "mass_flow": "Mass_Flow_kg_s",
                    "moisture": "Moisture_pct",
                    "swath": "Swath_m",
                    "speed": "Speed_kmh",
                    "heading": "Heading_deg",
                    "header": "Header",
                    "gnss_quality": "GPS_Fix",
                },
                "speed_unit": "km/h",
            }
        ],
        "scale_tickets": "scale_tickets.csv",
        "output_dir": "output",
    }
    path = out / "field.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path
