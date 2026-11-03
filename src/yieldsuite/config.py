"""Field-year configuration (one YAML file per field and harvest year)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .levels import ProductClass, classify_product
from .registry import MachineProfile

DEFAULT_SLACK_CHANNEL = "#yield-data-suite"


@dataclass
class HarvestLog:
    path: Path
    machine_id: str
    format: str = "isoxml"  # isoxml | csv
    # csv: map canonical names (time, x, y | lon, lat, mass_flow, moisture, swath,
    # speed, heading, header, gnss_quality) to the file's column names
    columns: dict[str, str] = field(default_factory=dict)
    entry_level: str = "L1B"  # L1B: mass flow, no delay applied; L2: vendor applied delay
    mass_flow_unit: str = "kg/s"  # kg/s | t/h
    speed_unit: str = "m/s"  # m/s | km/h
    heading_unit: str = "deg"  # deg | rad, clockwise from north
    position_crs: str | None = None  # CRS of x/y if not the field CRS


@dataclass
class ImagerySource:
    kind: str  # sentinel2 | drone
    path: Path | None = None  # local orthomosaic (drone)
    date: str | None = None
    # Sentinel-2 via openEO
    start: str | None = None  # ISO date; default <year>-04-15
    end: str | None = None  # ISO date, exclusive; default <year>-09-15
    indices: tuple[str, ...] | None = None
    max_cloud_cover: float = 80.0
    min_valid_share: float = 0.8  # drop dates where fewer cells are cloud-free
    backend: str = "openeo.dataspace.copernicus.eu"


@dataclass
class SlackSettings:
    channel: str = DEFAULT_SLACK_CHANNEL
    webhook_url_env: str = "YIELDSUITE_SLACK_WEBHOOK_URL"
    bot_token_env: str = "YIELDSUITE_SLACK_BOT_TOKEN"
    viewer_base_url: str | None = None


@dataclass
class FieldYearConfig:
    field_id: str
    year: int
    boundary: Path | None = None
    crs: str = "EPSG:3067"
    grid_resolution_m: float = 20.0
    grid_origin: tuple[float, float] = (0.0, 0.0)
    reference_moisture_pct: float = 14.0
    harvest_logs: list[HarvestLog] = field(default_factory=list)
    scale_tickets: Path | None = None
    imagery: list[ImagerySource] = field(default_factory=list)
    machines: dict[str, MachineProfile] = field(default_factory=dict)
    output_dir: Path = Path("output")
    slack: SlackSettings | None = None

    @property
    def sentinel2(self) -> ImagerySource | None:
        return next((i for i in self.imagery if i.kind == "sentinel2"), None)

    @property
    def product_class(self) -> ProductClass:
        return classify_product(
            has_harvester=bool(self.harvest_logs),
            has_scale_weights=self.scale_tickets is not None,
            has_imagery=bool(self.imagery),
        )

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the config is usable."""
        problems: list[str] = []
        if self.grid_resolution_m <= 0:
            problems.append("grid_resolution_m must be positive")
        if not 0 <= self.reference_moisture_pct < 100:
            problems.append("reference_moisture_pct must be in [0, 100)")
        if not self.harvest_logs and not self.imagery:
            problems.append("need at least one harvest log or imagery source")
        for img in self.imagery:
            if img.kind not in ("sentinel2", "drone"):
                problems.append(f"unknown imagery kind {img.kind!r}")
        for log in self.harvest_logs:
            if log.machine_id not in self.machines:
                problems.append(f"harvest log {log.path} uses unknown machine {log.machine_id!r}")
        return problems


def _path(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


def load_config(path: str | Path) -> FieldYearConfig:
    path = Path(path)
    base = path.parent
    raw = yaml.safe_load(path.read_text()) or {}

    machines = {m["machine_id"]: MachineProfile.from_dict(m) for m in raw.get("machines", [])}
    logs = []
    for h in raw.get("harvest_logs", []):
        h = dict(h)
        h["path"] = _path(base, h["path"])
        h["columns"] = dict(h.get("columns") or {})
        logs.append(HarvestLog(**h))
    imagery = []
    for i in raw.get("imagery", []):
        i = dict(i)
        i["path"] = _path(base, i.get("path"))
        for key in ("date", "start", "end"):
            if i.get(key) is not None:
                i[key] = str(i[key])
        if i.get("indices") is not None:
            i["indices"] = tuple(i["indices"])
        imagery.append(ImagerySource(**i))
    slack_raw = (raw.get("notify") or {}).get("slack")
    slack = (
        SlackSettings(**slack_raw)
        if isinstance(slack_raw, dict)
        else (SlackSettings() if slack_raw else None)
    )
    grid = raw.get("grid", {})
    return FieldYearConfig(
        field_id=str(raw["field_id"]),
        year=int(raw["year"]),
        boundary=_path(base, raw.get("boundary")),
        crs=raw.get("crs", "EPSG:3067"),
        grid_resolution_m=float(grid.get("resolution_m", 20.0)),
        grid_origin=tuple(float(v) for v in grid.get("origin", (0.0, 0.0))),
        reference_moisture_pct=float(raw.get("reference_moisture_pct", 14.0)),
        harvest_logs=logs,
        scale_tickets=_path(base, raw.get("scale_tickets")),
        imagery=imagery,
        machines=machines,
        output_dir=_path(base, raw.get("output_dir", "output")),
        slack=slack,
    )
