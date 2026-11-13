"""2D harvest timelapse: the chosen maps filling in as the combine drives.

Each frame adds the records cut since the previous frame (record polygons coloured
by the layer: yield, moisture, protein, oil ...), side by side in a grid, and draws
the combine at its position with the header as a bar across the swath: solid green
when lowered (harvesting), dashed red when raised. A layer that was not measured is
shown as a panel with the reason. Written as MP4 with ffmpeg (GIF without it).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .timelapse import PALETTE, Track, choose_layers, colour_range


def _hillshade(dem: np.ndarray, res: float, az: float = 315, alt: float = 45) -> np.ndarray:
    gy, gx = np.gradient(dem, res)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    a, z = np.deg2rad(az), np.deg2rad(90 - alt)
    return np.cos(z) * np.cos(slope) + np.sin(z) * np.sin(slope) * np.cos(a - aspect)


def _header_bar(x, y, h, w):
    rx, ry = np.cos(h), -np.sin(h)
    return [x - rx * w / 2, x + rx * w / 2], [y - ry * w / 2, y + ry * w / 2]


def _combine_shape(x, y, h, size=4.0):
    """Arrow behind the header, pointing in the driving direction; ``size`` is its
    width (length 2.4 x size), scaled up from the real 4 m so it shows on the map."""
    fx, fy = np.sin(h), np.cos(h)
    rx, ry = np.cos(h), -np.sin(h)
    back = (x - fx * 2.6 * size, y - fy * 2.6 * size)
    tip = (x - fx * 0.2 * size, y - fy * 0.2 * size)
    return [tip, (back[0] + rx * size / 2, back[1] + ry * size / 2),
            (back[0] - rx * size / 2, back[1] - ry * size / 2)]


def grid_shape(n: int) -> tuple[int, int]:
    """(rows, columns) for n panels."""
    if n <= 3:
        return 1, n
    cols = 2 if n == 4 else 3
    return -(-n // cols), cols


def write_video(
    track: Track,
    path: str | Path,
    boundary=None,
    dem_path: str | Path | None = None,
    seconds: float = 24.0,
    fps: int = 25,
    lang: str = "fi",
    tz: str = "Europe/Helsinki",
    layers: list[str] | None = None,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib import animation
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import Normalize
    from matplotlib.patches import Polygon as MplPolygon

    fi = lang == "fi"
    path = Path(path)
    keys = choose_layers(track, layers)
    n_frames = int(seconds * fps)
    end = float(track.play[-1]) if len(track.play) else 1.0
    edges = np.searchsorted(track.play, np.linspace(0, end, n_frames + 1)[1:], side="right")
    rows, cols = grid_shape(len(keys))
    fig, axes = plt.subplots(rows, cols, figsize=(6.4 * cols, 6.3 * rows), squeeze=False)
    axes_flat = list(axes.ravel())
    ok = np.isfinite(track.x) & np.isfinite(track.y)
    pad = 25
    x0, x1 = np.nanmin(track.x[ok]) - pad, np.nanmax(track.x[ok]) + pad
    y0, y1 = np.nanmin(track.y[ok]) - pad, np.nanmax(track.y[ok]) + pad
    if boundary is not None:
        bx0, by0, bx1, by1 = boundary.total_bounds
        x0, y0, x1, y1 = min(x0, bx0 - 10), min(y0, by0 - 10), max(x1, bx1 + 10), max(y1, by1 + 10)
    shade = None
    if dem_path and Path(dem_path).exists():
        import rasterio
        from rasterio.plot import plotting_extent

        with rasterio.open(dem_path) as r:
            dem = r.read(1, masked=True).filled(np.nan)
            shade = (_hillshade(dem, abs(r.res[0])), plotting_extent(r))
    sym = max(4.0, (x1 - x0) / 45)  # combine symbol width on the map (m)
    machines = sorted(set(track.machine))
    polys_xy = [np.asarray(p.exterior.coords)[:4] if not p.is_empty else None
                for p in track.polygons]
    panels = []  # (ax, key, values, cmap, norm, {machine: (triangle, bar)})
    for ax, key in zip(axes_flat, keys, strict=False):
        layer = track.layers[key]
        label = layer.label(lang)
        if shade is not None:
            ax.imshow(shade[0], extent=shade[1], cmap="Greys_r", alpha=0.45, zorder=0)
        if boundary is not None:
            boundary.boundary.plot(ax=ax, color="k", lw=0.8, zorder=3)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(label)
        if not layer.available:
            ax.text(0.5, 0.5, (layer.reason if fi else layer.reason_en or layer.reason),
                    transform=ax.transAxes, ha="center", va="center", fontsize=10, wrap=True,
                    color="#868e96", bbox=dict(fc="white", ec="#ced4da", alpha=0.9, pad=8))
            continue
        lo, hi = colour_range(layer.values)
        norm = Normalize(lo, hi)
        cmap = PALETTE[key]
        fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.7, label=label)
        cb = {}
        for m in machines:
            tri = MplPolygon([[0, 0], [0, 0], [0, 0]], closed=True, fc="#212529", ec="w",
                             lw=1.2, zorder=6)
            ax.add_patch(tri)
            (bar,) = ax.plot([], [], lw=5, solid_capstyle="butt", zorder=7,
                             path_effects=[pe.Stroke(linewidth=8, foreground="w"), pe.Normal()])
            cb[m] = (tri, bar)
        panels.append((ax, key, layer.values, cmap, norm, cb))
    for ax in axes_flat[len(keys):]:
        ax.axis("off")
    title = fig.text(0.5, 0.985, "", ha="center", va="top", fontsize=13)
    legend = (
        "leikkuupöytä: vihreä = alhaalla (puinti), punainen katkoviiva = ylhäällä · "
        "harmaa = leikattu, tietue hylätty tai ei mittausta"
        if fi else "header: green = lowered (harvesting), dashed red = raised · "
        "grey = cut, record flagged or no reading"
    )
    fig.text(0.5, 0.008, legend, ha="center", fontsize=10, color="#495057")
    fig.tight_layout(rect=(0, 0.025, 1, 0.965))
    state = {"done": 0}

    def frame(k):
        stop = int(edges[k])
        start = state["done"]
        if stop > start:
            sl = slice(start, stop)
            cut = track.cut[sl] if track.cut is not None else np.ones(stop - start, bool)
            for ax, _key, vals, cmap, norm, _cb in panels:
                v = vals[sl]
                sel = [i for i, (pv, vv) in enumerate(zip(polys_xy[sl], v, strict=True))
                       if pv is not None and np.isfinite(vv)]
                grey = [i for i, pv in enumerate(polys_xy[sl])
                        if pv is not None and cut[i] and not np.isfinite(v[i])]
                if grey:
                    ax.add_collection(PolyCollection([polys_xy[sl][i] for i in grey],
                                                     facecolors="#9e9e9e", alpha=0.45,
                                                     edgecolors="none", zorder=1.5))
                if sel:
                    ax.add_collection(PolyCollection(
                        [polys_xy[sl][i] for i in sel],
                        facecolors=plt.get_cmap(cmap)(norm(v[sel])), edgecolors="none", zorder=2))
            state["done"] = stop
        i_last = max(stop - 1, 0)
        for m in machines:
            idx = np.flatnonzero(track.machine[: i_last + 1] == m)
            if not idx.size:
                continue
            j = idx[-1]
            if not (np.isfinite(track.x[j]) and np.isfinite(track.heading[j])):
                continue
            active = track.play[i_last] - track.play[j] < 30
            down = bool(track.header_down[j])
            w = float(track.swath[j]) if np.isfinite(track.swath[j]) else 6.0
            bx, by = _header_bar(track.x[j], track.y[j], track.heading[j], max(w, sym * 1.6))
            for _ax, _key, _v, _c, _n, cb in panels:
                tri, bar = cb[m]
                tri.set_xy(_combine_shape(track.x[j], track.y[j], track.heading[j], sym))
                tri.set_alpha(1.0 if active else 0.35)
                bar.set_data(bx, by)
                bar.set_color("#2b8a3e" if down else "#e03131")
                bar.set_linestyle("-" if down else (0, (2, 1.2)))
        ts = datetime.fromtimestamp(float(track.t[i_last]), tz=timezone.utc)
        try:
            from zoneinfo import ZoneInfo

            ts = ts.astimezone(ZoneInfo(tz))
        except Exception:  # noqa: BLE001
            pass
        title.set_text(ts.strftime("%d.%m.%Y %H:%M"))
        return []

    anim = animation.FuncAnimation(fig, frame, frames=n_frames, blit=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if animation.writers.is_available("ffmpeg") and path.suffix.lower() == ".mp4":
        writer = animation.FFMpegWriter(fps=fps, bitrate=-1,
                                        extra_args=["-pix_fmt", "yuv420p", "-crf", "27",
                                                    "-movflags", "+faststart"])
    else:
        path = path.with_suffix(".gif")
        writer = animation.PillowWriter(fps=min(fps, 15))
    anim.save(path, writer=writer, dpi=90)
    plt.close(fig)
    return path
