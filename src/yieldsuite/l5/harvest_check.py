"""Was the unlogged part of the field harvested? A look at Sentinel-2 and Landsat.

When the critical review finds field area without harvest records, the open
question is whether it was harvested without logging (or is missing from the
export) or left standing. Harvest is not seen directly but by its result:
ripe crop turns into stubble, which lowers NDTI (= NBR2, SWIR1 vs SWIR2) and
usually NDVI.

Method, per image family (Sentinel-2, Landsat 8/9, PlanetScope if present) so
that no cross-sensor calibration is needed:

1. *before*: the last clear scene before the first logged harvest day;
   *after*: clear scenes from the day after the last logged harvest day on.
2. The logged area (records, interior cells only) is the harvested reference.
   Its change ``d_H = after - before`` (median of NDVI and NDTI) is compared with
   its own cell-to-cell spread ``s``; if ``|d_H / s| < 2`` the pair cannot show
   the harvest and is not used.
3. Every unlogged cell gets the normalised projection of its own change on the
   harvest change, ``p = (d / s) . (d_H / s) / |d_H / s|^2``: about 1 when it
   changed like the harvested area, about 0 when it stayed as it was. A cell
   whose *before* value already looked like stubble (projection of
   ``before - before_H`` >= 0.6) was harvested before the logged harvest.
4. Only "pure" cells are classified: cells at least half a source pixel inside
   both the field and the unlogged area. Unlogged strips narrower than about
   two source pixels (20 m Sentinel-2 SWIR, 30 m Landsat) cannot be judged;
   for them, and when no usable scene pair exists, a PlanetScope scene (3 m)
   after the harvest is suggested.

Classes: harvested (p >= 0.6 in some after scene, with the first such date),
harvested_before (stubble already before the logged harvest), not_harvested
(p <= 0.3 in every usable after scene), unclear.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

HARVESTED, BEFORE, STANDING, UNCLEAR = "harvested", "harvested_before", "not_harvested", "unclear"
P_HARVEST, P_STANDING = 0.6, 0.3
MIN_SEPARATION = 2.0
MIN_PURE_CELLS = 3

LABEL_FI = {
    HARVESTED: "puitu (ei satokartoitusta)",
    BEFORE: "puitu jo ennen kirjattua puintia",
    STANDING: "näyttää puimattomalta",
    UNCLEAR: "epäselvä",
}


@dataclass
class Settings:
    enabled: str = "auto"  # auto: when the unlogged check is watch/alert; true; false
    days_before: int = 25
    days_after: int = 30
    families: tuple = ("S2", "LS")
    planet_suggest: bool = True
    planet_search: bool = True  # free search of PlanetScope candidates (no quota)


def indices(bands: dict) -> dict:
    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        if "red" in bands and "nir" in bands:
            out["NDVI"] = (bands["nir"] - bands["red"]) / (bands["nir"] + bands["red"])
        if "swir1" in bands and "swir2" in bands:
            out["NDTI"] = (bands["swir1"] - bands["swir2"]) / (bands["swir1"] + bands["swir2"])
    return out


def _erode(mask: np.ndarray, cells: int) -> np.ndarray:
    if cells <= 0:
        return mask.copy()
    from scipy import ndimage

    y, x = np.ogrid[-cells : cells + 1, -cells : cells + 1]
    return ndimage.binary_erosion(mask, structure=(x * x + y * y) <= cells * cells)


def _mad(v: np.ndarray) -> float:
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else np.nan


def pair_projection(before: dict, after: dict, harvested: np.ndarray, target: np.ndarray):
    """Projections of the change in ``target`` cells on the harvested-area change.

    Returns (p_after, p_before, info); p arrays are NaN outside ``target`` or
    where an index is missing, info holds d_H, spread and separation.
    """
    keys = [k for k in ("NDVI", "NDTI") if k in before and k in after]
    d = {k: after[k] - before[k] for k in keys}
    dh, s, bh = {}, {}, {}
    for k in keys:
        v = d[k][harvested]
        v = v[np.isfinite(v)]
        if v.size < 10:
            return None, None, {"reason": "too few clear harvested cells"}
        dh[k] = float(np.median(v))
        s[k] = max(_mad(v), 0.01)
        bh[k] = float(np.nanmedian(before[k][harvested]))
    z = np.array([dh[k] / s[k] for k in keys])
    sep = float(np.sqrt((z**2).sum()))
    info = {"d_harvested": dh, "spread": s, "separation": sep, "indices": keys}
    if sep < MIN_SEPARATION:
        info["reason"] = f"harvest change not visible (separation {sep:.1f} < {MIN_SEPARATION})"
        return None, None, info
    num = sum((d[k] / s[k]) * (dh[k] / s[k]) for k in keys)
    p_after = np.where(target, num / sep**2, np.nan)
    # before value relative to the harvested area's before value, on the same axis
    num_b = sum(((before[k] - bh[k]) / s[k]) * (dh[k] / s[k]) for k in keys)
    p_before = np.where(target, num_b / sep**2, np.nan)
    return p_after, p_before, info


def classify_cells(pairs: list[tuple[str, np.ndarray, np.ndarray]], target: np.ndarray):
    """Combine pair projections (date, p_after, p_before) into classes per cell.

    Returns (classes array of str with '' outside target, first harvest date array).
    """
    cls = np.full(target.shape, "", dtype=object)
    first = np.full(target.shape, "", dtype=object)
    if not pairs:
        cls[target] = UNCLEAR
        return cls, first
    seen = np.zeros(target.shape, bool)
    any_h = np.zeros(target.shape, bool)
    all_low = np.ones(target.shape, bool)
    before_like = np.zeros(target.shape, bool)
    for dt, pa, pb in sorted(pairs, key=lambda x: x[0]):
        ok = np.isfinite(pa)
        newly = ok & (pa >= P_HARVEST) & ~any_h
        first[newly] = dt
        any_h |= ok & (pa >= P_HARVEST)
        all_low &= ~ok | (pa <= P_STANDING)
        before_like |= ok & np.isfinite(pb) & (pb >= P_HARVEST) & (pa <= P_STANDING)
        seen |= ok
    cls[target] = UNCLEAR
    cls[target & seen & all_low] = STANDING
    cls[target & seen & before_like] = BEFORE
    cls[target & seen & any_h] = HARVESTED
    return cls, first


def regions(mask: np.ndarray, min_cells: int = 2) -> list[np.ndarray]:
    from scipy import ndimage

    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    return [lab == i for i in range(1, n + 1) if (lab == i).sum() >= min_cells]


def check(
    scenes: list,
    grid,
    field_mask: np.ndarray,
    logged: np.ndarray,
    unlogged: np.ndarray,
    harvest_start: date,
    harvest_end: date,
    settings: Settings | None = None,
) -> dict:
    """Classify the unlogged area from scenes (``public.optical.Scene``); ``settings``
    choose the scenes upstream and are accepted here for symmetry."""
    ha = grid.cell_area / 1e4
    by_fam: dict[str, list] = {}
    for sc in scenes:
        by_fam.setdefault(sc.family, []).append(sc)
    pairs_used, pairs_rejected, cells_cls = [], [], {}
    pure_any = np.zeros(unlogged.shape, bool)
    best_cls = np.full(unlogged.shape, "", dtype=object)
    best_first = np.full(unlogged.shape, "", dtype=object)
    for fam, lst in by_fam.items():
        lst = sorted(lst, key=lambda x: x.date)
        before = [x for x in lst if date.fromisoformat(x.date) < harvest_start]
        after = [x for x in lst if date.fromisoformat(x.date) > harvest_end]
        if not before or not after:
            pairs_rejected.append(
                {"family": fam, "reason": "no clear scene " + ("before" if not before else "after")}
            )
            continue
        k = int(np.ceil(lst[0].res_m / 2 / grid.resolution - 1e-9)) - (
            1 if lst[0].res_m <= grid.resolution else 0
        )
        k = max(k, 0)
        pure_u = _erode(unlogged & field_mask, k) & field_mask
        pure_h = _erode(logged & field_mask, k)
        pure_any |= pure_u
        b = before[-1]
        bi = indices(b.bands)
        fam_pairs = []
        for a in after:
            pa, pb, info = pair_projection(bi, indices(a.bands), pure_h, pure_u)
            rec = {"family": fam, "before": b.date, "after": a.date, **info}
            if pa is None:
                pairs_rejected.append(rec)
                continue
            rec["pure_unlogged_cells_clear"] = int(np.isfinite(pa).sum())
            pairs_used.append(rec)
            fam_pairs.append((a.date, pa, pb))
        if fam_pairs:
            cls, first = classify_cells(fam_pairs, pure_u)
            cells_cls[fam] = cls
            # finer family wins where it has a verdict
            take = (cls != "") & ((best_cls == "") | (best_cls == UNCLEAR))
            best_cls[take] = cls[take]
            best_first[take] = first[take]
    region_rows = []
    unl = unlogged & field_mask
    for i, reg in enumerate(regions(unl), start=1):
        c = best_cls[reg]
        c = c[c != ""]
        n_pure = int(c.size)
        counts = {x: int((c == x).sum()) for x in (HARVESTED, BEFORE, STANDING, UNCLEAR)}
        decided = {x: v for x, v in counts.items() if x != UNCLEAR and v}
        if n_pure < MIN_PURE_CELLS:
            verdict = "too_narrow"
        elif decided and max(decided.values()) >= 0.6 * n_pure:
            verdict = max(decided, key=decided.get)
        else:
            verdict = UNCLEAR
        firsts = sorted({str(x) for x in best_first[reg] if x})
        rr, cc = np.nonzero(reg)
        region_rows.append(
            {
                "region": i,
                "area_ha": round(float(reg.sum() * ha), 3),
                "pure_cells": n_pure,
                "counts": counts,
                "verdict": verdict,
                "harvested_by": firsts[0] if firsts else None,
                "centre_xy": [
                    float(grid.xmin + (cc.mean() + 0.5) * grid.resolution),
                    float(grid.ymax - (rr.mean() + 0.5) * grid.resolution),
                ],
            }
        )
    area = {v: 0.0 for v in (HARVESTED, BEFORE, STANDING, UNCLEAR, "too_narrow")}
    for r in region_rows:
        area[r["verdict"]] += r["area_ha"]
    total = float(unl.sum() * ha)
    need_planet = bool(
        total > 0
        and (not pairs_used or (area["too_narrow"] + area[UNCLEAR]) >= 0.3 * total)
    )
    return {
        "harvest_window": [str(harvest_start), str(harvest_end)],
        "unlogged_ha": round(total, 3),
        "area_ha_by_verdict": {k: round(v, 3) for k, v in area.items()},
        "regions": region_rows,
        "pairs_used": pairs_used,
        "pairs_rejected": pairs_rejected,
        "scenes": [
            {"family": sc.family, "date": sc.date, "platform": sc.platform, "id": sc.item_id}
            for sc in scenes
        ],
        "planet_suggested": need_planet,
        "_classes": best_cls,
    }


def finding(res: dict, planet: dict | None) -> tuple[str, str, str]:
    """(severity, fi, en) for the review table."""
    a = res["area_ha_by_verdict"]
    tot = res["unlogged_ha"]
    fams = sorted({p["family"] for p in res["pairs_used"]})
    fam_txt = {"S2": "Sentinel-2", "LS": "Landsat", "PS": "PlanetScope"}
    used = ", ".join(fam_txt[f] for f in fams) or "–"
    if not res["pairs_used"]:
        fi = (
            "Puuttuvan alueen puintia ei voitu tarkistaa: ennen ja jälkeen puinnin ei ole "
            "selkeää Sentinel-2- tai Landsat-paria, jossa puitu ala erottuu."
        )
        en = (
            "The unlogged area could not be checked: no clear Sentinel-2 or Landsat pair "
            "before and after the harvest in which the harvested area stands out."
        )
        sev = "watch"
    else:
        parts_fi, parts_en = [], []
        names_en = {
            HARVESTED: "harvested (not logged)",
            BEFORE: "harvested before the logged harvest",
            STANDING: "looks unharvested",
            UNCLEAR: "unclear",
            "too_narrow": "too narrow for 20-30 m pixels",
        }
        for k in (STANDING, HARVESTED, BEFORE, UNCLEAR, "too_narrow"):
            if a.get(k):
                label = LABEL_FI.get(k, "liian kapea 20–30 m pikseleille")
                parts_fi.append(f"{label} {a[k]:.2f} ha")
                parts_en.append(f"{names_en[k]} {a[k]:.2f} ha")
        fi = f"Puuttuva ala {tot:.2f} ha satelliittikuvista ({used}): " + ", ".join(parts_fi) + "."
        en = f"Unlogged {tot:.2f} ha from satellite images ({used}): " + ", ".join(parts_en) + "."
        sev = "alert" if a.get(STANDING, 0) >= 0.1 else "watch" if res["planet_suggested"] else "ok"
    if res["planet_suggested"] and planet:
        fi += " " + planet["fi"]
        en += " " + planet["en"]
    return sev, fi, en


def planet_suggestion(
    harvest_end: date,
    field_area_ha: float,
    candidates: list[dict] | None = None,
    search_error: str | None = None,
) -> dict:
    """Text and commands for a PlanetScope scene after the harvest (3 m)."""
    day = harvest_end + timedelta(days=1)
    km2 = round(field_area_ha / 100 * 1.15, 3)  # clipped to the field + 10 m margin
    out = {
        "first_day": str(day),
        "quota_km2_per_scene": km2,
        "commands": [
            f"yieldsuite planet search field.yaml --day {day}",
            "yieldsuite planet order field.yaml --items <id> --confirm",
        ],
        "candidates": candidates or [],
    }
    best = [c for c in (candidates or []) if (c.get("parcel_clear_pct") or 0) >= 95 and c["has_sr"]]
    if best:
        c = best[0]
        out["fi"] = (
            f"Tarkista PlanetScope-kuvalla (3 m): esim. {c['id']} ({c['acquired'][:10]}, lohko "
            f"{c['parcel_clear_pct']:.0f} % selkeä), kiintiötä noin {km2} km²."
        )
        out["en"] = (
            f"Check with a PlanetScope scene (3 m): e.g. {c['id']} ({c['acquired'][:10]}, field "
            f"{c['parcel_clear_pct']:.0f} % clear), about {km2} km² of quota."
        )
    else:
        why = f" (haku: {search_error})" if search_error else ""
        why_en = f" (search: {search_error})" if search_error else ""
        out["fi"] = (
            f"Ehdotus: PlanetScope-kuva (3 m) {day} alkaen, kiintiötä noin {km2} km² kuvaa "
            f"kohden; hae `yieldsuite planet search field.yaml --day {day}`{why}."
        )
        out["en"] = (
            f"Suggestion: a PlanetScope scene (3 m) from {day} on, about {km2} km² of quota per "
            f"scene; search `yieldsuite planet search field.yaml --day {day}`{why_en}."
        )
    return out


def search_planet(geom_wgs84: dict, harvest_end: date, days: int = 10, max_check: int = 6):
    """Free PlanetScope search after the harvest; parcel clear share for the best scenes."""
    from ..public import planet

    items = planet.search(geom_wgs84, harvest_end + timedelta(days=1),
                          harvest_end + timedelta(days=days + 1))
    rows = []
    for f in sorted(items, key=lambda f: -(f["properties"].get("clear_percent") or 0))[:max_check]:
        p = f["properties"]
        assets = set(f.get("assets", []))
        rows.append(
            {
                "id": f["id"],
                "acquired": p.get("acquired", ""),
                "scene_clear_pct": p.get("clear_percent"),
                "parcel_clear_pct": planet.parcel_clear(f["id"], geom_wgs84),
                "has_sr": bool(assets & {"ortho_analytic_4b_sr", "ortho_analytic_8b_sr"}),
            }
        )
    return sorted(rows, key=lambda r: (-(r["parcel_clear_pct"] or -1), r["acquired"]))


def figure(res: dict, scenes: list, grid, boundary, path, lang: str = "fi", field_mask=None,
           unlogged=None):
    """After-harvest NDTI (or NDVI) with the unlogged area and the verdict per cell."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    used = res["pairs_used"]
    fi = lang == "fi"
    pick = None
    if used:
        u = sorted(used, key=lambda p: (p["family"] != "S2", p["after"]))[0]
        pick = next(
            (s for s in scenes if s.family == u["family"] and s.date == u["after"]), None
        )
        before = next(
            (s for s in scenes if s.family == u["family"] and s.date == u["before"]), None
        )
    ext = (grid.xmin, grid.xmin + grid.nx * grid.resolution,
           grid.ymax - grid.ny * grid.resolution, grid.ymax)
    fig, ax = plt.subplots(1, 3, figsize=(15, 5))
    for a, sc, title in ((ax[0], before if pick else None, "ennen" if fi else "before"),
                         (ax[1], pick, "jälkeen" if fi else "after")):
        if sc is not None:
            ix = indices(sc.bands)
            key = "NDTI" if "NDTI" in ix else "NDVI"
            v = ix[key] if field_mask is None else np.where(field_mask, ix[key], np.nan)
            im = a.imshow(v, extent=ext, cmap="BrBG", vmin=-0.05, vmax=0.25)
            fig.colorbar(im, ax=a, shrink=0.75, label=key)
            a.set_title(f"{key} {sc.date} ({title}, {sc.family})")
    cls = res["_classes"]
    code = {HARVESTED: 1, BEFORE: 2, STANDING: 3, UNCLEAR: 4}
    m = np.full(cls.shape, np.nan)
    for k, v in code.items():
        m[cls == k] = v
    cmap = ListedColormap(["#2b8a3e", "#74b816", "#c92a2a", "#adb5bd"])
    ax[2].imshow(m, extent=ext, cmap=cmap, vmin=0.5, vmax=4.5, interpolation="nearest")
    ax[2].set_title("puuttuvan alueen luokka" if fi else "unlogged area class")
    from matplotlib.patches import Patch

    lab = LABEL_FI if fi else {HARVESTED: "harvested", BEFORE: "harvested before",
                                STANDING: "not harvested", UNCLEAR: "unclear"}
    handles = [Patch(color=cmap(i), label=lab[k]) for i, k in enumerate(code)]
    if unlogged is not None:
        from matplotlib.lines import Line2D

        handles.append(Line2D([], [], color="m", lw=1.2,
                              label="ei puintitietueita" if fi else "no records"))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), fontsize=9,
               frameon=False, bbox_to_anchor=(0.5, -0.04))
    if field_mask is not None:
        ax[2].imshow(np.where(field_mask, 0.0, np.nan), extent=ext, cmap="Greys", vmin=0,
                     vmax=4, alpha=0.6)
        ax[2].imshow(m, extent=ext, cmap=cmap, vmin=0.5, vmax=4.5, interpolation="nearest")
    for a in ax:
        if unlogged is not None and unlogged.any():
            xs = grid.xmin + (np.arange(grid.nx) + 0.5) * grid.resolution
            ys = grid.ymax - (np.arange(grid.ny) + 0.5) * grid.resolution
            a.contour(xs, ys, unlogged.astype(float), levels=[0.5], colors="m", linewidths=1.0)
        if boundary is not None:
            boundary.boundary.plot(ax=a, color="k", lw=0.7)
        a.set_xticks([])
        a.set_yticks([])
        a.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path
