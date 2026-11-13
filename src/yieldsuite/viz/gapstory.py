"""Gap-fill story: how the empty parts of the yield map are filled, step by step.

A sketch of a timelapse in five parts, built from the L5 outputs of a field-year:

1. the measured map (cells with harvester records); the rest is empty;
2. the sources, one map each: every Sentinel-2 date (NDVI, 10 m) and PlanetScope scene
   that fed the model, in date order;
3. the image model: its prediction appears in the empty cells (cross-validated error);
4. the kriging correction: the difference between measured yield and prediction is spread
   from the measured cells into the gaps, cell by cell by distance (the colour of each gap
   cell moves from the prediction to the final value);
5. the finished map, the filled part marked as an estimate.

The same story is written as a self-contained 3D page (``story3d.html``) and as a video.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NDVI_RANGE = (0.2, 0.9)
FRAME_S = 1.4  # seconds per Sentinel-2 date
PLANET_S = 2.0


@dataclass
class Frame:
    source: str  # sentinel2 | planetscope
    date: str
    values: np.ndarray  # (ny, nx) NDVI, NaN where not clear
    final_image: bool = False
    note: str = ""
    range: tuple = (0.2, 0.9)  # colour range of this image (2nd-98th percentile)


def _range(v) -> tuple:
    v = v[np.isfinite(v)]
    if v.size < 10:
        return NDVI_RANGE
    lo, hi = np.percentile(v, [2, 98])
    if hi - lo < 0.05:
        mid = (hi + lo) / 2
        lo, hi = mid - 0.025, mid + 0.025
    return (float(lo), float(hi))


@dataclass
class Story:
    grid: object
    crs: str
    field_id: str
    year: int
    mask: np.ndarray  # field cells
    measured: np.ndarray  # yield where measured, else NaN
    pred: np.ndarray  # image-model prediction
    final: np.ndarray  # measured + filled
    sigma: np.ndarray
    gap: np.ndarray  # field cells that were filled
    empty: np.ndarray  # field cells with no value at all
    rank: np.ndarray  # 0..1 order of the gap cells by distance to the nearest measured cell
    frames: list[Frame] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    chapters: list[dict] = field(default_factory=list)
    yrange: tuple = (0.0, 1.0)


def _fmt_date(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{int(dd)}.{int(m)}.{y}"


def load_story(config) -> Story:
    import json

    from ..l4.io import read_bands
    from ..l5.sentinel2 import load_features

    out = Path(config.output_dir)
    base = f"{config.field_id}_{config.year}"
    bands, grid, crs = read_bands(out / f"L5_{base}.tif", require_yield=False)
    card = json.loads((out / f"L5_{base}_model_card.json").read_text())
    ny, nx = grid.ny, grid.nx
    if config.boundary is not None and Path(config.boundary).exists():
        from ..l5.fuse import boundary_mask
        from ..public.boundary import load_boundary

        mask = boundary_mask(load_boundary(config.boundary, config.crs), grid)
    else:
        mask = np.isfinite(bands["yield"])
    src = np.nan_to_num(bands["source"], nan=0).astype(int)
    final = np.where(mask & np.isfinite(bands["yield"]), bands["yield"], np.nan)
    measured = np.where(mask & (src == 1), final, np.nan)
    gap = mask & (src >= 2) & np.isfinite(final)
    interp = mask & (src == 3)
    empty = mask & ~np.isfinite(final)
    pred = np.where(mask, bands["prediction"], np.nan)
    sigma = np.where(gap, bands["sigma"], np.nan)
    # order of the filling: by distance to the nearest measured cell
    from scipy import ndimage

    dist = ndimage.distance_transform_edt(~np.isfinite(measured))
    rank = np.zeros((ny, nx))
    if gap.any():
        order = np.argsort(np.argsort(dist[gap], kind="stable"), kind="stable")
        rank[gap] = order / max(order.size - 1, 1)
    # sources: Sentinel-2 NDVI per used date, PlanetScope NDVI per scene
    frames: list[Frame] = []
    feats_path = out / f"L5_{base}_s2_features.csv"
    s2 = card.get("sentinel2") or {}
    final_date = s2.get("final_image_date") or (card.get("feature_set") or {}).get(
        "final_image_date"
    )
    if feats_path.exists():
        f = load_features(feats_path)
        f = f[f["index"] == "NDVI"]
        for d in sorted(s2.get("dates_used") or f["date"].unique()):
            sub = f[f["date"] == d]
            if not len(sub):
                continue
            a = np.full(ny * nx, np.nan)
            a[sub["cell"].to_numpy()] = sub["value"].to_numpy()
            vals = np.where(mask, a.reshape(ny, nx), np.nan)
            frames.append(
                Frame("sentinel2", d, vals, final_image=(d == final_date), range=_range(vals))
            )
    harm = ((card.get("sources") or {}).get("harmonisation") or {}).get("sources", {})
    for img in config.imagery:
        if img.kind != "planetscope" or not img.path or not img.date:
            continue
        try:
            from ..l5.raster_features import raster_features

            df, _ = raster_features(
                img.path,
                grid,
                config.crs,
                str(img.date),
                source="planetscope",
                sensor=img.sensor or "superdove",
                bands=img.bands or None,
                scale=img.scale,
                mask_path=img.mask,
                mask_clear_band=img.mask_clear_band,
            )
        except Exception:  # noqa: BLE001 - a scene that cannot be read is left out of the story
            continue
        sub = df[df["index"] == "NDVI"]
        if not len(sub):
            continue
        a = np.full(ny * nx, np.nan)
        a[sub["cell"].to_numpy()] = sub["value"].to_numpy()
        how = (harm.get("planetscope", {}).get("indices", {}).get("NDVI", {}) or {}).get("method")
        vals = np.where(mask, a.reshape(ny, nx), np.nan)
        frames.append(
            Frame("planetscope", str(img.date), vals, note=how or "", range=_range(vals))
        )
    frames.sort(key=lambda fr: (fr.date, fr.source))
    ha = grid.cell_area / 1e4
    n_field = int(mask.sum())
    fs = card.get("feature_set") or {}
    chosen = fs.get("chosen", "")
    cv = (
        fs.get("cv_rmse_season_and_final_image")
        if "final image" in chosen
        else fs.get("cv_rmse_season")
    )
    lu = card.get("light_use") or {}
    facts = {
        "measured_ha": float(np.isfinite(measured).sum() * ha),
        "gap_ha": float(gap.sum() * ha),
        "interp_ha": float(interp.sum() * ha),
        "empty_ha": float(empty.sum() * ha),
        "field_ha": float(n_field * ha),
        "cv_rmse": float(cv) if cv is not None else None,
        "feature_set": chosen,
        "final_image_date": final_date,
        "sigma_median": float(np.nanmedian(sigma)) if np.isfinite(sigma).any() else None,
        "sigma_max": float(np.nanmax(sigma)) if np.isfinite(sigma).any() else None,
        "fill_used": lu.get("fill_used", "image"),
        "n_s2": sum(fr.source == "sentinel2" for fr in frames),
        "n_planet": sum(fr.source == "planetscope" for fr in frames),
        "mean_pred_gap": float(np.nanmean(pred[gap])) if gap.any() else None,
        "mean_final_gap": float(np.nanmean(final[gap])) if gap.any() else None,
        "mean_measured": float(np.nanmean(measured)) if np.isfinite(measured).any() else None,
    }
    vals = final[np.isfinite(final)]
    yr = (float(np.percentile(vals, 2)), float(np.percentile(vals, 98))) if vals.size else (0, 1)
    story = Story(
        grid,
        crs,
        config.field_id,
        config.year,
        mask,
        measured,
        pred,
        final,
        sigma,
        gap,
        empty,
        rank,
        frames,
        facts,
        [],
        yr,
    )
    story.chapters = chapters(story)
    return story


def chapters(s: Story) -> list[dict]:
    """The story as a list of chapters with durations and captions (fi/en)."""
    f = s.facts
    pct = 100 * f["measured_ha"] / max(f["field_ha"], 1e-9)
    ch = [
        {
            "kind": "measured",
            "dur": 5.0,
            "title": {"fi": "1 · Mitattu satokartta", "en": "1 · Measured yield map"},
            "text": {
                "fi": f"Puimurin tietueista saatiin mitattu sato {f['measured_ha']:.1f} ha:lle "
                f"({pct:.0f} % lohkosta). Tyhjää (harmaa) on {f['gap_ha'] + f['empty_ha']:.1f} ha.",
                "en": f"The harvester records give a measured yield for {f['measured_ha']:.1f} ha "
                f"({pct:.0f} % of the field). Empty (grey): {f['gap_ha'] + f['empty_ha']:.1f} ha.",
            },
        }
    ]
    for i, fr in enumerate(s.frames):
        d = _fmt_date(fr.date)
        if fr.source == "sentinel2":
            fi = f"Sentinel-2 L2A (ESA/Copernicus) · {d} · NDVI 10 m, pilvet maskattu"
            en = f"Sentinel-2 L2A (ESA/Copernicus) · {d} · NDVI 10 m, clouds masked"
            if fr.final_image:
                fi += " · mallin loppukuva (viimeinen kuva ennen puintia)"
                en += " · the model's final image (last image before harvest)"
        else:
            how = {
                "own fit": "oma sovitus Sentinel-2:een",
                "vendor": "toimittajan harmonisointi",
            }.get(fr.note, "harmonisoitu Sentinel-2:een")
            how_en = {"own fit": "own fit to Sentinel-2", "vendor": "provider's harmonisation"}.get(
                fr.note, "harmonised to Sentinel-2"
            )
            fi = f"PlanetScope 3 m (Planet Labs) · {d} · NDVI, {how}"
            en = f"PlanetScope 3 m (Planet Labs) · {d} · NDVI, {how_en}"
        ch.append(
            {
                "kind": "source",
                "frame": i,
                "dur": PLANET_S if fr.source == "planetscope" else FRAME_S,
                "title": {"fi": "2 · Lähteet", "en": "2 · Sources"},
                "text": {"fi": fi, "en": en},
            }
        )
    cv = f["cv_rmse"]
    cvt = f"{cv:.2f} t/ha" if cv is not None else "–"
    ch.append(
        {
            "kind": "model",
            "dur": 5.0,
            "title": {"fi": "3 · Kuvamalli", "en": "3 · Image model"},
            "text": {
                "fi": f"Malli oppii mitatuista ruuduista, miten kasvuston kuvasignaali ({f['n_s2']} Sentinel-2-päivää"
                f"{' + ' + str(f['n_planet']) + ' PlanetScope' if f['n_planet'] else ''}) liittyy satoon, ja "
                f"ennustaa sadon tyhjille alueille. Ristiinvalidoitu virhe {cvt}.",
                "en": f"The model learns from the measured cells how the crop's image signal ({f['n_s2']} Sentinel-2 dates"
                f"{' + ' + str(f['n_planet']) + ' PlanetScope' if f['n_planet'] else ''}) relates to yield, and "
                f"predicts yield for the empty parts. Cross-validated error {cvt}.",
            },
        }
    )
    sg = f["sigma_median"]
    sgt = f"{sg:.2f} t/ha" if sg is not None else "–"
    ch.append(
        {
            "kind": "krige",
            "dur": 7.0,
            "title": {"fi": "4 · Kriging-korjaus", "en": "4 · Kriging correction"},
            "text": {
                "fi": "Mitatun sadon ja kuvamallin ero levitetään tyhjiin ruutuihin krigingillä mitatuilta alueilta sisäänpäin: "
                f"ruutujen arvo siirtyy ennusteesta lopulliseen. Kapeat aukot ({f.get('interp_ha', 0):.1f} ha) täytetään suoraan mitatuista naapureista. Täytettyjen ruutujen epävarmuus {sgt} (mediaani).",
                "en": "The difference between measured yield and the image-model prediction is spread into the empty cells by "
                "kriging, inwards from the measured areas: each cell moves from the prediction to the final value. "
                f"Uncertainty of the filled cells {sgt} (median).",
            },
        }
    )
    ch.append(
        {
            "kind": "final",
            "dur": 5.0,
            "title": {"fi": "5 · Valmis kartta", "en": "5 · Finished map"},
            "text": {
                "fi": f"Mitattu {f['measured_ha']:.1f} ha + täytetty {f['gap_ha']:.1f} ha. Täytetty osa (vaalennettu) on arvio, ei mittaus."
                + (f" Harmaa ({f['empty_ha']:.1f} ha): ei arvoa, kuvapiirteet puuttuvat." if f["empty_ha"] > 0.05 else "")
                + (f" Täyttö: {f['fill_used']}." if f["fill_used"] not in ("image", None) else ""),
                "en": f"Measured {f['measured_ha']:.1f} ha + filled {f['gap_ha']:.1f} ha. The filled part (lightened) is an estimate, "
                "not a measurement."
                + (f" Grey ({f['empty_ha']:.1f} ha): no value, image features missing." if f["empty_ha"] > 0.05 else "")
                + (f" Fill: {f['fill_used']}." if f["fill_used"] not in ("image", None) else ""),
            },
        }
    )
    return ch


# --- cell colours (the 3D page does the same in JavaScript) ---------------------------


def _cmap(name):
    import matplotlib

    return matplotlib.colormaps[name]


def cell_rgba(s: Story, ch: dict, u: float) -> np.ndarray:
    """(ny, nx, 4) float colours for chapter ``ch`` at progress ``u`` in [0, 1]."""
    ny, nx = s.mask.shape
    out = np.zeros((ny, nx, 4))
    yl, yh = s.yrange
    yc = _cmap("RdYlGn")

    def col(v):
        return yc(np.clip((v - yl) / (yh - yl), 0, 1))

    kind = ch["kind"]
    grey = np.array([0.67, 0.67, 0.67, 0.65])
    meas = np.isfinite(s.measured)
    if kind == "source":
        v = s.frames[ch["frame"]].values
        lo, hi = s.frames[ch["frame"]].range
        ok = s.mask & np.isfinite(v)
        out[ok] = _cmap("RdYlGn")(np.clip((v[ok] - lo) / (hi - lo), 0, 1))
        out[s.mask & ~np.isfinite(v)] = grey
        return out
    out[meas] = col(s.measured[meas])
    gapm = s.gap | s.empty
    out[gapm] = grey
    if kind == "measured":
        return out
    if kind == "model":
        c = col(s.pred[s.gap])
        c[:, 3] = u
        base = np.tile(grey, (int(s.gap.sum()), 1))
        out[s.gap] = base * (1 - u) + c * u
        return out
    if kind == "krige":
        w = 0.25
        p = np.clip((u * (1 + w) - s.rank) / w, 0, 1)[s.gap]
        v = s.pred[s.gap] + (s.final[s.gap] - s.pred[s.gap]) * p
        c = col(v)
        edge = ((p > 0) & (p < 1))[:, None]
        c[:, :3] = np.where(edge, c[:, :3] * 0.6 + 0.4, c[:, :3])  # the moving front is lighter
        out[s.gap] = c
        return out
    if kind == "final":
        c = col(s.final[s.gap])
        c[:, :3] = c[:, :3] * 0.8 + 0.2  # filled cells lightened: estimate
        out[s.gap] = c
        return out
    return out


def write_video(
    s: Story, path, boundary=None, dem_path=None, lang: str = "fi", fps: int = 20
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import textwrap

    import matplotlib.pyplot as plt
    from matplotlib import animation

    from .video import _hillshade

    path = Path(path)
    g = s.grid
    ext = (g.xmin, g.xmin + g.nx * g.resolution, g.ymax - g.ny * g.resolution, g.ymax)
    fig = plt.figure(figsize=(12.5, 6.8))
    ax = fig.add_axes([0.02, 0.1, 0.58, 0.82])
    tx = fig.add_axes([0.62, 0.1, 0.36, 0.82])
    tx.axis("off")
    if dem_path and Path(dem_path).exists():
        import rasterio
        from rasterio.plot import plotting_extent

        with rasterio.open(dem_path) as r:
            dem = r.read(1, masked=True).filled(np.nan)
            ax.imshow(
                _hillshade(dem, abs(r.res[0])), extent=plotting_extent(r), cmap="Greys_r", alpha=0.5
            )
    im = ax.imshow(np.zeros((g.ny, g.nx, 4)), extent=ext, interpolation="nearest", zorder=2)
    if boundary is not None:
        boundary.boundary.plot(ax=ax, color="k", lw=0.9, zorder=3)
    ax.set_xlim(ext[0] - 20, ext[1] + 20)
    ax.set_ylim(ext[2] - 20, ext[3] + 20)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    cax = fig.add_axes([0.2, 0.05, 0.3, 0.018])
    chs = s.chapters
    total = sum(c["dur"] for c in chs)
    ticks = np.cumsum([0] + [c["dur"] for c in chs])
    bar = fig.add_axes([0.62, 0.05, 0.36, 0.018])
    bar.set_xlim(0, total)
    bar.set_ylim(0, 1)
    bar.axis("off")
    for t in ticks[:-1]:
        bar.plot([t, t], [0, 1], color="#868e96", lw=1)
    (head,) = bar.plot([0, 0], [0, 1], color="#c92a2a", lw=3)
    n_frames = int(total * fps)
    state = {"cb": None}

    def legend(kind, frame=None):
        key = (kind, frame)
        if state["cb"] == key:
            return
        cax.clear()
        state["cb"] = key
        if kind == "source":
            lo, hi = s.frames[frame].range
            lab = "NDVI"
        else:
            lo, hi, lab = (
                s.yrange[0],
                s.yrange[1],
                ("sato (t/ha)" if lang == "fi" else "yield (t/ha)"),
            )
        cax.imshow(
            np.linspace(0, 1, 256)[None, :], cmap="RdYlGn", aspect="auto", extent=[lo, hi, 0, 1]
        )
        cax.set_yticks([])
        cax.set_xlabel(lab, fontsize=9)

    def frame(k):
        t = k / fps
        i = int(np.searchsorted(ticks, t, side="right") - 1)
        i = min(max(i, 0), len(chs) - 1)
        ch = chs[i]
        u = min(1.0, (t - ticks[i]) / ch["dur"])
        im.set_data(cell_rgba(s, ch, u))
        legend(ch["kind"], ch.get("frame"))
        head.set_xdata([t, t])
        tx.clear()
        tx.axis("off")
        tx.text(0, 1, ch["title"][lang], fontsize=15, fontweight="bold", va="top")
        tx.text(
            0,
            0.9,
            "\n".join(textwrap.wrap(ch["text"][lang], 52)),
            fontsize=11,
            va="top",
            linespacing=1.4,
        )
        if s.frames:
            srcs = [(j, c) for j, c in enumerate(chs) if c["kind"] == "source"]
            tx.text(
                0,
                0.46,
                "Lähteet" if lang == "fi" else "Sources",
                fontsize=10,
                color="#495057",
                va="top",
            )
            for n, (j, c) in enumerate(srcs):
                fr = s.frames[c["frame"]]
                active = ch["kind"] == "source" and ch["frame"] == c["frame"]
                tag = "S2" if fr.source == "sentinel2" else "PS"
                col = "#c92a2a" if active else ("#212529" if t >= ticks[j] else "#adb5bd")
                tx.text(
                    0,
                    0.41 - n * 0.032,
                    f"{'▶' if active else '·'} {tag} {_fmt_date(fr.date)}"
                    + ("  ★" if fr.final_image else ""),
                    fontsize=9,
                    color=col,
                    va="top",
                    family="monospace",
                )
        return []

    anim = animation.FuncAnimation(fig, frame, frames=n_frames, blit=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if animation.writers.is_available("ffmpeg") and path.suffix.lower() == ".mp4":
        writer = animation.FFMpegWriter(
            fps=fps,
            bitrate=-1,
            extra_args=["-pix_fmt", "yuv420p", "-crf", "27", "-movflags", "+faststart"],
        )
    else:
        path = path.with_suffix(".gif")
        writer = animation.PillowWriter(fps=min(fps, 12))
    anim.save(path, writer=writer, dpi=90)
    plt.close(fig)
    return path


def build_html(
    s: Story, path, boundary=None, dem_path=None, title: str = "", lang: str = "fi"
) -> Path:
    """Self-contained 3D page of the story (terrain, cells painted per chapter)."""
    from .viewer3d import TEMPLATE_DIR, VENDOR, _terrain, pack

    g = s.grid
    pad = 40.0
    x0, x1 = g.xmin - pad, g.xmin + g.nx * g.resolution + pad
    y0, y1 = g.ymax - g.ny * g.resolution - pad, g.ymax + pad
    h, attribution = _terrain(dem_path, x0, y0, x1, y1, 4.0)
    nyh, nxh = h.shape
    base = float(np.nanmin(h))

    def arr(a, d=2):
        return [None if not np.isfinite(v) else round(float(v), d) for v in a.ravel()]

    ring = []
    if boundary is not None:
        from shapely.geometry import MultiPolygon

        u = boundary.union_all() if hasattr(boundary, "union_all") else boundary.unary_union
        for part in u.geoms if isinstance(u, MultiPolygon) else [u]:
            for rr in [part.exterior, *part.interiors]:
                ring.append(
                    [round(float(v), 1) for xy in rr.coords for v in (xy[0] - x0, xy[1] - y0)]
                )
    data = {
        "title": title,
        "lang": lang,
        "terrain": {
            "nx": nxh,
            "ny": nyh,
            "step": 4.0,
            "base": round(base, 2),
            "h": [round(float(v) - base, 1) for v in h.ravel()],
            "attribution": attribution,
        },
        "grid": {
            "nx": g.nx,
            "ny": g.ny,
            "res": g.resolution,
            "gx0": round(g.xmin - x0, 2),
            "gy1": round(g.ymax - y0, 2),
        },
        "mask": [int(v) for v in s.mask.ravel()],
        "measured": arr(s.measured),
        "pred": arr(s.pred),
        "final": arr(s.final),
        "gap": [int(v) for v in (s.gap | s.empty).ravel()],
        "rank": [round(float(v), 3) for v in s.rank.ravel()],
        "frames": [
            {"source": fr.source, "date": fr.date, "values": arr(fr.values, 2), "range": [round(v, 3) for v in fr.range]} for fr in s.frames
        ],
        "chapters": [
            {
                "kind": c["kind"],
                "dur": c["dur"],
                "frame": c.get("frame"),
                "title": c["title"][lang],
                "text": c["text"][lang],
            }
            for c in s.chapters
        ],
        "yrange": [round(v, 3) for v in s.yrange],
        "ndvi": list(NDVI_RANGE),
        "boundary": ring,
    }
    html = (TEMPLATE_DIR / "story3d.html").read_text(encoding="utf-8")
    html = html.replace("/*THREE*/", (VENDOR / "three.min.js").read_text(encoding="utf-8"))
    html = html.replace("/*ORBIT*/", (VENDOR / "OrbitControls.js").read_text(encoding="utf-8"))
    html = html.replace("/*COMMON*/", (TEMPLATE_DIR / "common3d.js").read_text(encoding="utf-8"))
    html = html.replace("/*DATA*/", pack(data))
    html = html.replace("__TITLE__", title or ("Täyttö" if lang == "fi" else "Gap fill"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
