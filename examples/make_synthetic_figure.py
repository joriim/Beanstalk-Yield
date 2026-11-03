"""Render docs/media/synthetic-field.png: truth, raw log, L4 map and Q4 protein.

Usage: python examples/make_synthetic_figure.py docs/media/synthetic-field.png
(needs matplotlib; writes a working copy to ./fig)
"""

import json
import sys
import numpy as np
import yaml
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.l4.io import read_bands
from yieldsuite.levels import Level
from yieldsuite.pipeline import run

out_png = sys.argv[1]
cfgp = synthetic.write_demo("fig", pass_gain_sd=0.0)
f = synthetic.make_field(0)
exp, lab = synthetic.quality_export(f)
exp.to_csv("fig/gs.csv", index=False)
lab.to_csv("fig/lab.csv", index=False)
raw = yaml.safe_load(cfgp.read_text())
raw["quality"] = {
    "sources": [{"path": "gs.csv", "machine_id": "combine-a"}],
    "lab_samples": "lab.csv",
    "n_factor": 5.7,
}
cfgp.write_text(yaml.safe_dump(raw))
s = run(load_config(cfgp), Level.L4)
assert s.ok, s.error
b4, g, _ = read_bands(s.outputs["L4"])
q4, _, _ = read_bands(s.outputs["Q4"], require_yield=False)
X0, Y0, W, H = synthetic.X0, synthetic.Y0, synthetic.FIELD_W, synthetic.FIELD_H
xx, yy = np.meshgrid(np.linspace(X0, X0 + W, 241), np.linspace(Y0, Y0 + H, 161))
truth = synthetic.true_yield(xx, yy)
log = f.log
on = log["Header"] == 1
raw_y = (
    10 * log["Mass_Flow_kg_s"] / (synthetic.SPEED * synthetic.HEADER_W)
)  # as logged, no corrections
ext_grid = [
    g.xmin - X0,
    g.xmin + g.nx * g.resolution - X0,
    g.ymax - g.ny * g.resolution - Y0,
    g.ymax - Y0,
]

fig, ax = plt.subplots(1, 4, figsize=(17, 4.3), constrained_layout=True)
kw = dict(cmap="RdYlGn", vmin=4.5, vmax=9.5)
im = ax[0].imshow(truth, origin="lower", extent=[0, W, 0, H], **kw)
ax[0].set_title("1. Todellinen sato (t/ha)")
ax[1].scatter(
    log["Easting"][on] - X0,
    log["Northing"][on] - Y0,
    c=raw_y[on],
    s=2,
    cmap="RdYlGn",
    vmin=4.5,
    vmax=11,
)
ax[1].set_title("2. Puimurin raakaloki\n(viive 12 s, +7 %, 18 % kosteus, piikit)")
ax[2].imshow(b4["yield"], origin="upper", extent=ext_grid, **kw)
ax[2].set_title("3. Ketjun L4-kartta 20 m (t/ha)\nRMSE 0,04 t/ha totuuteen")
im3 = ax[3].imshow(q4["protein_pct_dm"], origin="upper", extent=ext_grid, cmap="viridis")
ax[3].set_title("4. Q4-proteiini (% ka.)\nGrainSense-tyyppinen analysaattori")
for a in ax:
    a.set_xlim(-5, W + 5)
    a.set_ylim(-5, H + 5)
    a.set_aspect("equal")
    a.set_xlabel("m")
fig.colorbar(im, ax=ax[:3], shrink=0.8, label="t/ha")
fig.colorbar(im3, ax=ax[3], shrink=0.8, label="% ka.")
fig.savefig(out_png, dpi=110)
print(json.dumps({"k": s.k_factors, "flagged": s.flagged_pct, "notes": s.notes}))
