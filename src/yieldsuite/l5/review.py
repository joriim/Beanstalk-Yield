"""Critical review of the yield map against the growth analysis.

The yield map and the canopy (image model, or light use when it fills) are
independent views of the same field. Where they disagree in a systematic way
the yield data are usually the suspect, because the image carries no machine
effects. Each check returns a metric, a threshold and a severity
(``ok`` / ``watch`` / ``alert``) with a short finding in Finnish and English:

1. drift   residual (yield - prediction) against harvest time: calibration drift
2. machine level difference between machines or tasks
3. direction residual on passes driven one way vs the other: delay or offset
4. speed   residual against ground speed: losses or flow lag at high speed
5. tilt    planar trend of the cell residual across the field
6. deficit clusters of cells far below the prediction under a good canopy:
           grain left on the field, header losses, blocked sensor
7. surplus clusters far above the prediction under a weak canopy: flow
           spikes, double counting
8. unlogged cells inside the field without any harvest record, and the
           grain they would hold at the predicted yield
9. edge    yield of the edge zone against the interior

The thresholds are deliberately simple; they rank where to look, they do not
prove a cause.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEVERITY = ("ok", "watch", "alert")


def _sev(value, watch, alert) -> str:
    v = abs(value) if value is not None and np.isfinite(value) else 0.0
    return "alert" if v >= alert else "watch" if v >= watch else "ok"


def _check(cid, value, unit, watch, alert, fi, en, **extra) -> dict:
    return {
        "id": cid,
        "value": None if value is None or not np.isfinite(value) else float(value),
        "unit": unit,
        "thresholds": {"watch": watch, "alert": alert},
        "severity": _sev(value, watch, alert),
        "finding": {"fi": fi, "en": en},
        **extra,
    }


def _cell_index(x, y, grid):
    c = np.floor((np.asarray(x) - grid.xmin) / grid.resolution).astype(int)
    r = np.floor((grid.ymax - np.asarray(y)) / grid.resolution).astype(int)
    ok = (c >= 0) & (c < grid.nx) & (r >= 0) & (r < grid.ny)
    return np.where(ok, r * grid.nx + c, -1)


def _clusters(mask2d: np.ndarray, min_cells: int) -> list[np.ndarray]:
    from scipy import ndimage

    lab, n = ndimage.label(mask2d, structure=np.ones((3, 3)))
    out = []
    for k in range(1, n + 1):
        idx = np.flatnonzero(lab.ravel() == k)
        if idx.size >= min_cells:
            out.append(idx)
    return out


def review(
    records: pd.DataFrame,
    grid,
    l4: dict,
    prediction: np.ndarray,
    field_mask: np.ndarray | None = None,
    edge_mask: np.ndarray | None = None,
    reference_name: str = "image model",
    min_cluster_cells: int = 4,
) -> dict:
    """Run the checks. ``prediction``: growth-based yield per cell (2-D, same grid)."""
    n = grid.ny * grid.nx
    pred = np.asarray(prediction, float).ravel()
    y4 = np.asarray(l4["yield"], float).ravel()
    inside = np.ones(n, bool) if field_mask is None else np.asarray(field_mask, bool).ravel()
    checks = []

    # --- record-level residuals (clean harvesting records) -----------------------
    rec = records
    flags = rec["flags"].to_numpy(np.int64)
    clean = rec["header_down"].to_numpy(bool) & (flags == 0) & np.isfinite(rec["yield"].to_numpy())
    h = rec["heading"].to_numpy(float)
    half = rec["length"].to_numpy(float) / 2 if "length" in rec else 0.0
    hx = rec["hx"].to_numpy(float) if "hx" in rec else rec["x"].to_numpy(float)
    hy = rec["hy"].to_numpy(float) if "hy" in rec else rec["y"].to_numpy(float)
    cx, cy = hx - np.sin(h) * half, hy - np.cos(h) * half
    ci = _cell_index(cx, cy, grid)
    use = clean & (ci >= 0)
    use[use] &= np.isfinite(pred[ci[use]])
    yr = rec["yield"].to_numpy(float)
    res = np.full(len(rec), np.nan)
    res[use] = yr[use] - pred[ci[use]]
    mean_y = float(np.nanmedian(yr[use])) if use.any() else np.nan

    # 1 drift
    t = rec["t"].to_numpy(float)
    if use.sum() > 50 and np.ptp(t[use]) > 1800:
        th = (t[use] - t[use].min()) / 3600.0
        slope, icpt = np.polyfit(th, res[use], 1)
        span_h = float(th.max())
        change = slope * span_h / mean_y * 100
        corr = float(np.corrcoef(th, res[use])[0, 1])
        checks.append(
            _check(
                "drift",
                change if abs(corr) >= 0.1 else 0.0,
                "% over harvest",
                5,
                10,
                f"Ennusteeseen nähden sato muuttuu puinnin aikana {change:+.1f} % "
                f"({span_h:.1f} h, r {corr:+.2f}). "
                + (
                    "Viittaa anturin kalibroinnin ajautumiseen tai olosuhteiden muutokseen."
                    if abs(change) >= 5 and abs(corr) >= 0.1
                    else "Ei merkittävää ajautumista."
                ),
                f"Relative to the prediction, yield changes {change:+.1f} % over the harvest "
                f"({span_h:.1f} h, r {corr:+.2f}). "
                + (
                    "Points to sensor calibration drift or changing conditions."
                    if abs(change) >= 5 and abs(corr) >= 0.1
                    else "No material drift."
                ),
                slope_t_ha_per_h=float(slope),
                corr=corr,
                hours=span_h,
            )
        )

    # 2 machine
    if "machine_id" in rec and rec.loc[use, "machine_id"].nunique() > 1:
        med = rec.loc[use].assign(r=res[use]).groupby("machine_id")["r"].median()
        diff = (med.max() - med.min()) / mean_y * 100
        checks.append(
            _check(
                "machine",
                diff,
                "%",
                3,
                6,
                f"Koneiden/tehtävien tasoero ennusteeseen nähden {diff:.1f} % "
                f"({', '.join(f'{k} {v:+.2f}' for k, v in med.items())} t/ha).",
                f"Level difference between machines/tasks relative to the prediction "
                f"{diff:.1f} % ({', '.join(f'{k} {v:+.2f}' for k, v in med.items())} t/ha).",
                medians_t_ha={str(k): float(v) for k, v in med.items()},
            )
        )

    # 3 direction: split passes by heading relative to the main direction
    if use.sum() > 50:
        ang = np.mod(h[use], np.pi)  # axis of travel
        main = np.angle(np.mean(np.exp(2j * ang))) / 2
        along = np.cos(h[use] - main)
        a, b = res[use][along > 0.7], res[use][along < -0.7]
        if a.size > 20 and b.size > 20:
            diff = (np.median(a) - np.median(b)) / mean_y * 100
            checks.append(
                _check(
                    "direction",
                    diff,
                    "%",
                    3,
                    6,
                    f"Vastakkaisiin suuntiin ajettujen kaistojen ero {diff:+.1f} %. "
                    + (
                        "Viittaa virtausviiveen tai antennin sijainnin (myös sivuttaissiirron) "
                        "virheeseen, tai rinteeseen."
                        if abs(diff) >= 3
                        else "Ei suuntavirhettä."
                    ),
                    f"Passes driven in opposite directions differ by {diff:+.1f} %. "
                    + (
                        "Points to a flow-delay or antenna-offset (incl. lateral) error, "
                        "or a slope."
                        if abs(diff) >= 3
                        else "No direction bias."
                    ),
                    n_each=[int(a.size), int(b.size)],
                )
            )

    # 4 speed
    sp = rec["speed"].to_numpy(float)
    ok = use & np.isfinite(sp)
    if ok.sum() > 50 and np.nanstd(sp[ok]) > 0.05:
        q1, q3 = np.nanpercentile(sp[ok], [25, 75])
        lo, hi = res[ok & (sp <= q1)], res[ok & (sp >= q3)]
        eff = (np.median(hi) - np.median(lo)) / mean_y * 100
        corr = float(np.corrcoef(sp[ok], res[ok])[0, 1])
        checks.append(
            _check(
                "speed",
                eff if abs(corr) >= 0.1 else 0.0,
                "%",
                5,
                10,
                f"Nopeimmalla neljänneksellä ({q3:.1f} m/s+) sato on {eff:+.1f} % eri tasolla "
                f"kuin hitaimmalla ({q1:.1f} m/s−), r {corr:+.2f}. "
                + (
                    "Mahdollisia leikkuu- tai puintitappioita tai virtausviiveen vaikutusta; "
                    "toisaalta harvassa kasvustossa ajetaan usein lujempaa, joten "
                    "syy-yhteyden suuntaa ei voi päätellä tästä."
                    if abs(eff) >= 5 and abs(corr) >= 0.1
                    else "Ei selvää nopeusvaikutusta."
                ),
                f"The fastest quarter ({q3:.1f} m/s+) differs by {eff:+.1f} % from the "
                f"slowest ({q1:.1f} m/s−), r {corr:+.2f}. "
                + (
                    "Possible header or threshing losses or flow-lag effects; operators also "
                    "drive faster in thin crop, so the direction of cause is open."
                    if abs(eff) >= 5 and abs(corr) >= 0.1
                    else "No clear speed effect."
                ),
                corr=corr,
            )
        )

    # --- cell-level residuals ------------------------------------------------------
    cell_res = np.where(inside & np.isfinite(y4) & np.isfinite(pred), y4 - pred, np.nan)
    ok = np.isfinite(cell_res)
    resid_sd = float(np.nanstd(cell_res)) if ok.any() else np.nan

    # 5 tilt
    rows, cols = np.divmod(np.arange(n), grid.nx)
    if ok.sum() > 30:
        X = np.c_[np.ones(ok.sum()), cols[ok] * grid.resolution, -rows[ok] * grid.resolution]
        coef, *_ = np.linalg.lstsq(X, cell_res[ok], rcond=None)
        fit = X @ coef
        r2 = 1 - np.sum((cell_res[ok] - fit) ** 2) / np.sum(
            (cell_res[ok] - cell_res[ok].mean()) ** 2
        )
        span = np.ptp(fit) / mean_y * 100
        az = (np.degrees(np.arctan2(coef[1], coef[2])) + 360) % 360
        checks.append(
            _check(
                "tilt",
                span if r2 >= 0.05 else 0.0,
                "% across field",
                8,
                15,
                f"Ennusteesta poikkeava tasainen kallistus {span:.1f} % lohkon yli "
                f"(suunta {az:.0f}°, selittää {100 * r2:.0f} % jäännöksestä). "
                + (
                    "Kartta on vino: tarkista kalibrointi, kosteus ja puintijärjestys."
                    if span >= 8 and r2 >= 0.05
                    else "Ei merkittävää kallistusta."
                ),
                f"A planar tilt against the prediction of {span:.1f} % across the field "
                f"(towards {az:.0f}°, explains {100 * r2:.0f} % of the residual). "
                + (
                    "The map is skewed: check calibration, moisture and harvest order."
                    if span >= 8 and r2 >= 0.05
                    else "No material tilt."
                ),
                r2=float(r2),
                azimuth_deg=float(az),
            )
        )

    def cluster_check(cid, mask, sign):
        cl = _clusters(mask.reshape(grid.ny, grid.nx), min_cluster_cells)
        cells = int(sum(c.size for c in cl))
        area_ha = cells * grid.cell_area / 1e4
        dev = float(np.nanmean(np.concatenate([cell_res[c] for c in cl]))) if cl else 0.0
        share = cells / max(int((inside & np.isfinite(y4)).sum()), 1) * 100
        items = [
            {
                "cells": int(c.size),
                "area_ha": c.size * grid.cell_area / 1e4,
                "mean_residual_t_ha": float(np.nanmean(cell_res[c])),
                "centre_xy": [
                    float(grid.xmin + (cols[c].mean() + 0.5) * grid.resolution),
                    float(grid.ymax - (rows[c].mean() + 0.5) * grid.resolution),
                ],
            }
            for c in sorted(cl, key=lambda c: -c.size)[:10]
        ]
        return share, area_ha, dev, cl, items

    if ok.sum() > 30 and resid_sd > 0:
        z = cell_res / resid_sd
        med_pred = np.nanmedian(pred[ok])
        deficit = np.nan_to_num(z) < -2
        deficit &= np.nan_to_num(pred) >= med_pred
        share, area, dev, cl_def, items = cluster_check("deficit", deficit, -1)
        checks.append(
            _check(
                "deficit",
                share,
                "% of measured cells",
                1,
                3,
                f"{len(cl_def)} yhtenäistä aluetta ({area:.2f} ha, {share:.1f} % mitatuista "
                f"soluista), joissa sato on selvästi ennustetta pienempi (keskimäärin "
                f"{dev:+.2f} t/ha) vaikka kasvusto oli hyvä. "
                + (
                    "Tarkista: viljaa jäänyt pellolle, leikkuupöydän tappiot, lakoalueet tai "
                    "anturin tukos."
                    if cl_def
                    else ""
                ),
                f"{len(cl_def)} contiguous areas ({area:.2f} ha, {share:.1f} % of measured "
                f"cells) where yield is far below the prediction (mean {dev:+.2f} t/ha) under a "
                f"good canopy. "
                + (
                    "Check: grain left on the field, header losses, lodging or a blocked sensor."
                    if cl_def
                    else ""
                ),
                clusters=items,
            )
        )
        surplus = (np.nan_to_num(z) > 2) & (np.nan_to_num(pred) <= med_pred)
        share, area, dev, cl_sur, items = cluster_check("surplus", surplus, 1)
        checks.append(
            _check(
                "surplus",
                share,
                "% of measured cells",
                1,
                3,
                f"{len(cl_sur)} aluetta ({area:.2f} ha), joissa sato on selvästi ennustetta "
                f"suurempi (keskimäärin {dev:+.2f} t/ha) heikon kasvuston kohdalla. "
                + ("Mahdollisia virtauspiikkejä tai kaksinkertaista laskentaa." if cl_sur else ""),
                f"{len(cl_sur)} areas ({area:.2f} ha) far above the prediction (mean "
                f"{dev:+.2f} t/ha) under a weak canopy. "
                + ("Possible flow spikes or double counting." if cl_sur else ""),
                clusters=items,
            )
        )
    else:
        cl_def = cl_sur = []

    # 8 unlogged
    nrec = np.asarray(l4.get("n_records", np.isfinite(l4["yield"])), float).ravel()
    unl = inside & ~(nrec > 0)
    share = unl.sum() / max(inside.sum(), 1) * 100
    grain_t = float(np.nansum(pred[unl]) * grid.cell_area / 1e4)  # t/ha x ha
    logged_t = float(np.nansum(np.where(inside & (nrec > 0), y4, np.nan)) * grid.cell_area / 1e4)
    checks.append(
        _check(
            "unlogged",
            share,
            "% of field",
            5,
            20,
            f"{share:.0f} % lohkon alasta ({unl.sum() * grid.cell_area / 1e4:.2f} ha) ilman "
            f"yhtään puintitietuetta. Ennusteen mukaan siellä olisi noin {grain_t:.1f} t viljaa "
            f"(mitatulla alalla {logged_t:.1f} t). "
            + (
                "Joko puitu ilman satokartoitusta, data puuttuu viennistä tai jäänyt puimatta."
                if share >= 5
                else ""
            ),
            f"{share:.0f} % of the field ({unl.sum() * grid.cell_area / 1e4:.2f} ha) has no "
            f"harvest record. At the predicted yield it would hold about {grain_t:.1f} t "
            f"(measured area {logged_t:.1f} t). "
            + (
                "Harvested without logging, missing from the export, or not harvested."
                if share >= 5
                else ""
            ),
            predicted_grain_t=grain_t,
            logged_grain_t=logged_t,
        )
    )

    # 9 edge
    if edge_mask is not None:
        em = np.asarray(edge_mask, bool).ravel() & inside & np.isfinite(y4)
        im = ~np.asarray(edge_mask, bool).ravel() & inside & np.isfinite(y4)
        if em.sum() > 10 and im.sum() > 10:
            d = (np.median(y4[em]) - np.median(y4[im])) / np.median(y4[im]) * 100
            dp = (np.nanmedian(pred[em]) - np.nanmedian(pred[im])) / np.nanmedian(pred[im]) * 100
            gap = d - dp
            checks.append(
                _check(
                    "edge",
                    gap,
                    "%",
                    10,
                    20,
                    f"Reunavyöhykkeen sato on {d:+.0f} % sisäosaan nähden; kasvuston perusteella "
                    f"odotettu ero {dp:+.0f} %. Ylimääräinen ero {gap:+.0f} % "
                    + (
                        "viittaa reunan mittausvirheeseen (vajaa leveys, kääntymiset)."
                        if abs(gap) >= 10
                        else "on pieni."
                    ),
                    f"The edge zone yields {d:+.0f} % vs the interior; the canopy suggests "
                    f"{dp:+.0f} %. The extra gap of {gap:+.0f} % "
                    + (
                        "points to edge measurement errors (partial swath, turns)."
                        if abs(gap) >= 10
                        else "is small."
                    ),
                )
            )

    order = {"alert": 0, "watch": 1, "ok": 2}
    checks.sort(key=lambda c: order[c["severity"]])
    return {
        "reference": reference_name,
        "records_used": int(use.sum()),
        "cells_compared": int(ok.sum()),
        "residual_sd_t_ha": resid_sd,
        "checks": checks,
        "summary": {s: sum(c["severity"] == s for c in checks) for s in SEVERITY},
        "_arrays": {
            "cell_residual": cell_res,
            "record_residual": res,
            "deficit": cl_def,
            "surplus": cl_sur,
        },
    }


def figure(result: dict, grid, boundary, records: pd.DataFrame, path, lang: str = "fi"):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fi = lang == "fi"
    arr = result["_arrays"]
    res2d = arr["cell_residual"].reshape(grid.ny, grid.nx)
    ext = (
        grid.xmin,
        grid.xmin + grid.nx * grid.resolution,
        grid.ymax - grid.ny * grid.resolution,
        grid.ymax,
    )
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.2))
    v = np.nanpercentile(np.abs(res2d), 98) if np.isfinite(res2d).any() else 1
    im = ax[0].imshow(res2d, extent=ext, cmap="RdBu", vmin=-v, vmax=v)
    fig.colorbar(im, ax=ax[0], shrink=0.8, label="t/ha")
    for key, colour in (("deficit", "k"), ("surplus", "m")):
        for c in arr[key]:
            r, cc = np.divmod(c, grid.nx)
            ax[0].scatter(
                grid.xmin + (cc + 0.5) * grid.resolution,
                grid.ymax - (r + 0.5) * grid.resolution,
                s=4,
                c=colour,
                marker="s",
            )
    if boundary is not None:
        boundary.boundary.plot(ax=ax[0], color="k", lw=0.6)
    ax[0].set_xticks([])
    ax[0].set_yticks([])
    ax[0].set_aspect("equal")
    ax[0].set_title(
        "Sato − kasvuennuste (musta: vaje, magenta: ylijäämä)"
        if fi
        else "Yield − growth prediction (black: deficit, magenta: surplus)"
    )
    rr = arr["record_residual"]
    ok = np.isfinite(rr)
    if ok.sum() > 10:
        t = (records["t"].to_numpy()[ok] - records["t"].to_numpy()[ok].min()) / 3600
        ax[1].scatter(t, rr[ok], s=2, alpha=0.25)
        k = max(1, ok.sum() // 60)
        order = np.argsort(t)
        tm = t[order]
        rm = rr[ok][order]
        ax[1].plot(
            tm[: len(tm) // k * k].reshape(-1, k).mean(1),
            rm[: len(rm) // k * k].reshape(-1, k).mean(1),
            color="k",
            lw=1.5,
        )
        ax[1].axhline(0, color="0.5", lw=0.8)
        ax[1].set_xlabel("h puinnin alusta" if fi else "h since harvest start")
        ax[1].set_ylabel("t/ha")
        ax[1].set_title(
            "Jäännös ajan funktiona (ajautuminen)" if fi else "Residual over time (drift)"
        )
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path
