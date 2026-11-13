"""Turn the long Sentinel-2 table into a feature matrix V_g (one row per grid cell)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FeatureInfo:
    dates_used: list[str] = field(default_factory=list)
    dates_dropped: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    last_date: str | None = None  # latest date feeding the ``<index>_last`` features


def feature_matrix(
    long_df: pd.DataFrame,
    n_cells: int,
    min_valid_share: float = 0.8,
    per_date: bool = False,
) -> tuple[pd.DataFrame, FeatureInfo]:
    """Feature matrix indexed by flat cell id 0..n_cells-1.

    Dates where fewer than ``min_valid_share`` of the cells have a value (clouds,
    partial tiles) are dropped; with a ``source`` column the share is taken
    against the cells each source covers. Features are, per index, the season
    mean and max over the kept dates and ``<index>_last``, the latest clear value
    of each cell (the canopy just before harvest); with ``per_date`` the individual
    dates are added as ``<index>_<YYYYMMDD>`` columns (useful for random forest).
    Cells without a value on some kept date get NaN and are left out of fitting.
    """
    info = FeatureInfo()
    if long_df.empty:
        return pd.DataFrame(index=pd.RangeIndex(n_cells, name="cell")), info

    season_dates = None
    if "source" in long_df.columns:
        # Validity per source, against the cells that source covers: a raster clipped
        # to the field (drone, PlanetScope) is not penalised against the larger grid.
        cover = long_df.groupby("source")["cell"].nunique()
        per = long_df.groupby(["source", "date"])["cell"].nunique()
        share = per / cover.reindex(per.index.get_level_values(0)).to_numpy()
        ok_pairs = share[share >= min_valid_share].index
        valid_dates = {d for _, d in ok_pairs}
        mask = pd.MultiIndex.from_frame(long_df[["source", "date"]]).isin(ok_pairs)
        all_dates = set(long_df["date"])
        long_df = long_df[mask]
        # Season features come from the reference (widest-covering) source's dates;
        # extra sources clipped to the field only feed the final image.
        ref = cover.idxmax()
        season_dates = set(long_df.loc[long_df["source"] == ref, "date"])
        keep = sorted(valid_dates)
        info.dates_used = keep
        info.dates_dropped = sorted(all_dates - set(keep))
        long_df = long_df.groupby(["cell", "date", "index"], as_index=False)["value"].mean()
        df = long_df
    else:
        n_aggregated = long_df["cell"].nunique()
        valid = long_df.groupby("date")["cell"].nunique() / max(n_aggregated, 1)
        keep = sorted(valid[valid >= min_valid_share].index)
        info.dates_used = keep
        info.dates_dropped = sorted(set(valid.index) - set(keep))
        df = long_df[long_df["date"].isin(keep)]

    wide = df.pivot_table(index="cell", columns=["index", "date"], values="value", aggfunc="mean")
    wide = wide.reindex(pd.RangeIndex(n_cells, name="cell"))

    parts = {}
    for name in sorted(df["index"].unique()):
        block = wide[name]
        # Season mean and max over the reference dates only: a raster clipped to the
        # field (drone, PlanetScope) must not take the other cells out.
        full = (
            [c for c in block.columns if c in season_dates] if season_dates else list(block.columns)
        )
        fb = block[full]
        complete = fb.notna().all(axis=1)
        parts[f"{name}_mean"] = fb.mean(axis=1).where(complete)
        parts[f"{name}_max"] = fb.max(axis=1).where(complete)
        if block.shape[1] >= 2:
            # "Final image": the latest clear observation of each cell, i.e. the
            # canopy just before harvest (ripening, lodging, late green patches);
            # where the last image is cloudy, the cell's previous clear value.
            ordered = block[sorted(block.columns)]
            parts[f"{name}_last"] = ordered.ffill(axis=1).iloc[:, -1].where(complete)
            info.last_date = max(info.last_date or "", sorted(block.columns)[-1])
        if per_date:
            for date in block.columns:
                parts[f"{name}_{date.replace('-', '')}"] = block[date]
    V = pd.DataFrame(parts, index=wide.index)
    info.columns = list(V.columns)
    return V, info


def complete_rows(V: pd.DataFrame) -> np.ndarray:
    """True for cells with every feature present."""
    if V.shape[1] == 0:
        return np.zeros(len(V), dtype=bool)
    return V.notna().all(axis=1).to_numpy()
