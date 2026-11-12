"""Command-line tool: ``yieldsuite``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import SlackSettings, load_config
from .levels import Level
from .pipeline import plan, run


def _cmd_validate(args) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if not problems:
        print(f"{cfg.field_id}/{cfg.year}: OK, product class {cfg.product_class.value}")
    return 1 if problems else 0


def _cmd_plan(args) -> int:
    cfg = load_config(args.config)
    print(f"{cfg.field_id}/{cfg.year} · product class {cfg.product_class.value}")
    for stage in plan(cfg, Level.parse(args.to), Level.parse(args.start)):
        status = "ready" if stage.implemented else "not implemented"
        print(f"  {stage.target.value:4} {stage.description}  [{status}]")
    return 0


def _cmd_run(args) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 1
    summary = run(cfg, Level.parse(args.to), Level.parse(args.start))
    if summary.ok:
        print(f"done: {', '.join(lv.value for lv in summary.completed)}")
        for name, path in summary.outputs.items():
            print(f"  {name:10} {path}")
        if summary.k_factors:
            print("  k factors  " + ", ".join(f"{m}={k:.3f}" for m, k in summary.k_factors.items()))
        if summary.flagged_pct is not None:
            print(f"  flagged    {summary.flagged_pct:.1f} % of harvesting records")
        if summary.mass_ok is not None:
            print(f"  mass check {'OK' if summary.mass_ok else 'FAIL'}")
    else:
        print(f"error: {summary.error}", file=sys.stderr)
    if args.notify == "slack":
        from .notify.slack import SlackNotifier

        SlackNotifier(cfg.slack or SlackSettings()).notify_run(summary)
    return 0 if summary.ok else 2


def _cmd_fuse(args) -> int:
    from .l4.io import read_bands
    from .l5.run import run_sentinel2_fusion
    from .pipeline import RunSummary

    cfg = load_config(args.config)
    l4, grid, raster_crs = read_bands(args.l4)
    if raster_crs and raster_crs.upper() != cfg.crs.upper():
        print(f"warning: L4 raster CRS {raster_crs} differs from config {cfg.crs}", file=sys.stderr)
    summary = RunSummary(cfg.field_id, cfg.year, Level.L5, cfg.product_class)
    try:
        outputs = run_sentinel2_fusion(
            cfg,
            l4,
            grid,
            features_path=args.features,
            out_dir=args.out,
            method=args.method,
            residual=args.residual,
            batch=args.batch,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the user and to Slack
        summary.error = f"L5 failed: {exc}"
    else:
        card = outputs.result.model_card
        fit, cells = card["fit"], card["cells"]
        summary.completed.append(Level.L5)
        summary.notes = [
            f"{len(card['sentinel2']['dates_used'])} clear S2 dates",
            f"R² {fit['r2']:.2f}",
            f"CV RMSE {fit.get('cv_rmse', float('nan')):.2f} t/ha",
            f"filled {100 * cells['filled_share']:.0f}% of cells",
        ]
        print(f"wrote {outputs.raster}\n      {outputs.model_card}")
        print(" · ".join(summary.notes))
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    if args.notify == "slack":
        from .notify.slack import SlackNotifier

        SlackNotifier(cfg.slack or SlackSettings()).notify_run(summary)
    return 0 if summary.ok else 2


def _cmd_demo(args) -> int:
    from .synthetic import write_demo

    path = write_demo(args.out_dir)
    print(f"wrote synthetic field-year to {path.parent}")
    print(f"next: yieldsuite run {path} --to L4")
    return 0


def _cmd_mcp(args) -> int:  # pragma: no cover - stdio loop
    from .mcp_server import main as mcp_main

    mcp_main(args.store)
    return 0


def _cmd_store_demo(args) -> int:
    from .store import init_demo_store

    root = init_demo_store(args.root)
    print(f"demo store at {root}: farm demo-farm, field synthetic-01, year 2026")
    print(f"next: yieldsuite mcp --store {root}")
    return 0


def _cmd_inspect(args) -> int:
    from .ingest.inspect import format_report, inspect_file

    kwargs = {
        k: v
        for k, v in {
            "format": args.format,
            "crop": args.crop,
            "machine_id": args.machine,
            "default_swath_m": args.swath,
            "position_crs": args.position_crs,
            "vendor_reference_moisture_pct": args.vendor_moisture,
            "units": dict(u.split("=", 1) for u in args.unit or []),
            "columns": dict(c.split("=", 1) for c in args.column or []),
        }.items()
        if v not in (None, {}, [])
    }
    try:
        result = inspect_file(args.path, crs=args.crs, **kwargs)
    except (ValueError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(result))
    return 0


def _cmd_weather(args) -> int:
    import json

    from .l4.io import read_bands
    from .weather.run import run_agroclimate, write_agroclimate

    cfg = load_config(args.config)
    grid = read_bands(args.l4)[1] if args.l4 else None
    try:
        result = run_agroclimate(cfg, grid, refresh=args.refresh)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    files = write_agroclimate(cfg, result, grid)
    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"wrote {files['agroclimate']} (weather cache {result['weather_file']})")
    return 0


def _cmd_delay(args) -> int:
    from .pipeline import estimate_delays

    cfg = load_config(args.config)
    for mid, d in estimate_delays(cfg).items():
        print(
            f"{mid}: profile {d['profile_tau_d_s']} s, estimated {d['estimated_tau_d_s']} s "
            f"({d['opposite_pass_pairs']} opposite-pass pairs, contrast "
            f"{(d['contrast'] or 0):.2f}){'' if d['identifiable'] else ' - ' + d['reason']}"
        )
        curve = {float(k): v for k, v in d["curve"].items() if v is not None}
        if curve:
            lo, hi = min(curve.values()), max(curve.values())
            for tau in sorted(curve)[::2]:
                bar = "#" * int(1 + 40 * (curve[tau] - lo) / ((hi - lo) or 1))
                print(f"  {tau:5.1f} s {bar}")
    return 0


def _cmd_machine(args) -> int:
    from . import inputs
    from .pipeline import estimate_offsets

    if args.action == "show":
        cfg = load_config(args.config)
        for mid, prof in cfg.machines.items():
            fwd, right = prof.antenna_offset_m
            print(
                f"{mid}: antenna forward {fwd:+.2f} m, right {right:+.2f} m, header "
                f"{prof.header_width_m} m, tau_d {prof.tau_d_s} s (v{prof.version})"
            )
        for e in inputs.read_log(args.config):
            if e["action"] == "set_machine":
                print(f"  {e['time']} {e['actor']}: {e['machine_id']} {e['old']} -> {e['new']}"
                      f" ({e['reason']})")
        return 0
    if args.action == "estimate-offset":
        res = estimate_offsets(load_config(args.config))
        for mid, d in res.items():
            if not d["identifiable"]:
                print(f"{mid}: not identifiable - {d['reason']}")
                continue
            lo, hi = d["ci95_m"]
            print(
                f"{mid}: passes sit {d['lateral_error_m']:+.2f} m to the right of the header "
                f"centre (95 % {lo:+.2f} .. {hi:+.2f}; {d['n_facing']} facing / "
                f"{d['n_back_to_back']} back-to-back pairs, spacing "
                f"{d['spacing_facing_m']:.2f} / {d['spacing_back_to_back_m']:.2f} m)"
            )
            if d["significant"]:
                print(
                    f"  suggestion: {_prog_name()} machine set {args.config} --machine {mid} "
                    f"--antenna-right {d['suggested_right_m']:+.2f} --reason \"estimated from "
                    f"opposite passes\"  (confirm with a tape measure first)"
                )
            else:
                print("  no correction needed (|e| < 0.1 m or not significant)")
        return 0
    change = inputs.set_machine(
        args.config,
        args.machine,
        antenna_forward_m=args.antenna_forward,
        antenna_right_m=args.antenna_right,
        header_width_m=args.header_width,
        tau_d_s=args.tau_d,
        reason=args.reason or "",
        actor=args.actor,
        dry_run=args.dry_run,
    )
    print(f"{change.machine_id}: {change.old} -> {change.new}"
          + (" (dry run, not written)" if args.dry_run else ""))
    if not args.dry_run:
        print(f"rerun: {_prog_name()} run {args.config} --to L5")
    return 0


def _cmd_tickets(args) -> int:
    from . import inputs

    if args.action == "add":
        if (args.mass_kg is None) == (args.mass_t is None):
            raise SystemExit("give exactly one of --mass-kg or --mass-t")
        row = {
            "mass_kg": args.mass_kg if args.mass_kg is not None else args.mass_t * 1000.0,
            "machine_id": args.machine,
            "moisture_pct": args.moisture,
            "field_share": args.share,
            "ticket_ref": args.ref,
            "date": args.date,
            "note": args.note,
        }
        inputs.add_tickets(args.config, [row], args.actor, args.reason or "")
    elif args.action == "import":
        mapping = dict(m.split("=", 1) for m in (args.map or []))
        rows = inputs.import_tickets(
            args.config, args.file, mapping, args.unit, args.machine, args.actor, args.reason or ""
        )
        print(f"imported {len(rows)} tickets")
    elif args.action == "remove":
        inputs.remove_ticket(args.config, args.ref, args.reason or "", args.actor)
        print(f"removed ticket {args.ref}")
    df = inputs.read_tickets(args.config)
    if args.action == "list" and len(df):
        shown = df.drop(columns=[c for c in ("added", "actor") if c in df.columns])
        print(shown.to_string(index=False))
    summ = inputs.ticket_summary(args.config)
    if len(summ):
        for _, r in summ.iterrows():
            print(
                f"{r['machine_id']}: {int(r['tickets'])} tickets, "
                f"{r['mass_kg'] / 1000:.2f} t weighed, "
                f"{r['mass_ref_kg'] / 1000:.2f} t at reference moisture"
            )
        if args.action != "list":
            print(f"rerun from L3: {_prog_name()} run {args.config} --to L5")
    else:
        print("no scale tickets yet")
    return 0


def _cmd_s2_search(args) -> int:
    from .l4.io import read_bands
    from .public.field import field_bbox_wgs84
    from .public.stac import search_sentinel2

    cfg = load_config(args.config)
    grid = read_bands(args.l4)[1] if args.l4 else None
    try:
        res = search_sentinel2(
            field_bbox_wgs84(cfg, grid),
            args.start or f"{cfg.year}-04-15",
            args.end or f"{cfg.year}-09-15",
            args.max_cloud,
            cfg.public_data.stac_url,
            cfg.public_data.s2_collection,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{res['n_below_max_cloud']}/{res['n_scenes']} scenes <= {args.max_cloud} % cloud; "
        f"{len(res['dates'])} dates"
    )
    for d in res["dates"]:
        print(f"  {d['date']}  {d['cloud_cover']:5.1f} %  {d['tile']}")
    utm = res.get("utm_epsg") or {}
    if utm:
        several = len(utm["zones_seen"]) > 1
        note = f"; tiles of several zones: {utm['zones_seen']}" if several else ""
        print(
            f"grid CRS aligned with these Sentinel-2 pixels: {utm['epsg']} "
            f"(UTM zone {utm['zone']}){note}"
        )
    return 0


def _cmd_zones(args) -> int:
    import json

    from .l4.io import read_bands
    from .zones import make_zones, place, union_grid, write_zones

    layers, grids, crs = [], [], args.crs
    for p in args.layers:
        b, g, c = read_bands(p)
        layers.append(b["yield"])
        grids.append(g)
        crs = crs or c
    covs = []
    for p in args.covariate or []:
        b, g, _ = read_bands(p, require_yield=False)
        covs += [(arr, g) for arr in b.values()]
        grids.append(g)
    target = union_grid(grids)
    try:
        res = make_zones(
            [place(a, g, target) for a, g in zip(layers, grids, strict=False)],
            target,
            [place(a, g, target) for a, g in covs],
            k_range=range(2, args.k_max + 1),
            names=[Path(p).stem for p in args.layers],
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    files = write_zones(res, target, crs, args.out, args.stem)
    s = res.summary
    print(
        f"{s['k']} zones from {len(layers)} years ({s['spatial_components']} spatial PCs); "
        f"zones explain {100 * (s['variance_explained_by_zones'] or 0):.0f} % of the "
        "variance of mean relative yield"
    )
    for z in s["zones"]:
        print(f"  zone {z['zone']}: {z['area_ha']:.2f} ha, {z['mean_relative_yield_pct']:.0f} %")
    print(
        "  k scan: "
        + ", ".join(f"k={r['k']} FPI {r['fpi']:.3f} NCE {r['nce']:.3f}" for r in s["k_scan"])
    )
    print(json.dumps(files, indent=2))
    return 0


def _cmd_grainsense(args) -> int:
    import json

    from .quality.grainsense import DEFAULT_BASE, GrainSenseClient

    client = GrainSenseClient(args.base or DEFAULT_BASE)
    try:
        if not client.token:
            client.login()
        if args.action == "sessions":
            for sess in client.flow_sessions(start=args.start, end=args.end):
                print(json.dumps(sess, default=str)[:400])
        elif args.action == "export":
            if not args.uuid or not args.out:
                print("error: export needs --uuid and --out", file=sys.stderr)
                return 1
            data = client.export_flow_sessions(args.uuid, args.format)
            Path(args.out).write_bytes(data)
            print(f"wrote {args.out} ({len(data)} bytes); add it under quality.sources")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_webodm(args) -> int:
    import json

    from .drone.webodm import WebODMClient, select_images

    try:
        client = WebODMClient(args.url)
        if args.action == "submit":
            files = select_images(args.images, not args.all_images)
            pid = client.project(args.project)
            tid = client.submit(
                pid,
                files,
                args.name or Path(args.images).name,
                progress=lambda d, n: print(f"  uploaded {d}/{n}"),
            )
            print(json.dumps({"project_id": pid, "task_id": tid, "images": len(files)}))
        elif args.action == "status":
            print(json.dumps(client.status(args.project_id, args.task)))
        elif args.action == "download":
            out = client.download(args.project_id, args.task, args.asset, args.out)
            print(f"wrote {out}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_parcel(args) -> int:
    import json

    from .public.parcels import RUOKAVIRASTO_WFS, ParcelService

    service = ParcelService(args.url or RUOKAVIRASTO_WFS)
    try:
        choice = service.choose(args.year, args.layer, args.id_column)
        print(f"layer {choice.typename} ({choice.reason}); id attribute {choice.id_column}")
        fc = service.fetch(args.ids, choice)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    if out.suffix.lower() == ".geojson":
        out.write_text(json.dumps(fc))
    else:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:3067")
        gdf.to_file(out, driver="GPKG")
        for _, r in gdf.iterrows():
            print(f"  {r[choice.id_column]}: {r.geometry.area / 1e4:.2f} ha")
    print(f"wrote {out}; use it as `boundary:` in field.yaml")
    return 0


def _cmd_terrain(args) -> int:
    import json

    from .covariates.terrain import TERRAIN_BANDS, terrain_covariates
    from .l4.io import read_bands, write_cog

    _, grid, crs = read_bands(args.like, require_yield=False)
    try:
        dem = args.dem
        if dem is None:
            from .public.nls_dem import NLSElevation, grid_bbox

            if (args.crs or crs).upper() != "EPSG:3067":
                raise ValueError("the NLS DEM is fetched in EPSG:3067; give a DEM file instead")
            dem = Path(args.out).with_name("dem_nls_2m.tif")
            NLSElevation().fetch(grid_bbox(grid, args.buffer + 40), dem)
            print(f"fetched {dem} (National Land Survey of Finland, CC BY 4.0)")
        bands, report = terrain_covariates(dem, grid, args.crs or crs, args.buffer)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = write_cog(bands, grid, args.crs or crs, args.out, names=TERRAIN_BANDS)
    print(json.dumps(report, indent=2))
    print(f"wrote {out}; use it as a zones covariate (--covariate {out})")
    return 0


def _cmd_notify_test(args) -> int:
    from .notify.slack import SlackNotifier

    settings = load_config(args.config).slack if args.config else None
    SlackNotifier(settings or SlackSettings()).send(
        f":seedling: yieldsuite {__version__} can post to this channel"
    )
    print("sent")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=_prog_name(), description=__doc__)
    p.add_argument("--version", action="version", version=f"yieldsuite {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check a field-year config")
    v.add_argument("config")
    v.set_defaults(func=_cmd_validate)

    for name, func, helptext in (
        ("plan", _cmd_plan, "show the stages a run would execute"),
        ("run", _cmd_run, "process a field-year"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("config")
        s.add_argument("--to", default="L4", help="last level to produce (default L4)")
        s.add_argument("--from", dest="start", default="L1A", help="first level to rebuild")
        if name == "run":
            s.add_argument("--notify", choices=["slack"], help="post a run summary")
        s.set_defaults(func=func)

    f = sub.add_parser("fuse", help="L4 -> L5: Sentinel-2 fusion via openEO")
    f.add_argument("config")
    f.add_argument("--l4", required=True, help="L4 GeoTIFF (band 'yield', optionally 'coverage')")
    f.add_argument("--features", help="cached feature CSV; fetched from openEO if missing")
    f.add_argument("--out", help="output directory (default: output_dir from config)")
    f.add_argument("--method", choices=["linear", "rf"], default="linear")
    f.add_argument("--residual", choices=["kriging", "none"], default="kriging")
    f.add_argument("--batch", action="store_true", help="run as an openEO batch job")
    f.add_argument("--notify", choices=["slack"], help="post a run summary")
    f.set_defaults(func=_cmd_fuse)

    d = sub.add_parser("demo", help="write a synthetic field-year to try the chain")
    d.add_argument("out_dir")
    d.set_defaults(func=_cmd_demo)

    i = sub.add_parser("inspect", help="detect format, columns and units of a data file")
    i.add_argument("path")
    i.add_argument("--crs", default="EPSG:3067", help="field CRS (default EPSG:3067)")
    i.add_argument("--format", default="auto")
    i.add_argument("--crop", help="crop name, needed for bu/ac yields")
    i.add_argument("--machine", help="machine id when the file has no machine column")
    i.add_argument("--swath", type=float, help="header width [m] when the file has none")
    i.add_argument("--position-crs", help="CRS of x/y columns if not the field CRS")
    i.add_argument(
        "--vendor-moisture",
        type=float,
        help="reference moisture [%%] of dry/standard yields in the file",
    )
    i.add_argument("--unit", action="append", help="confirm a unit, e.g. --unit speed=mph")
    i.add_argument("--column", action="append", help="map a column, e.g. --column yield_dry=Sato")
    i.set_defaults(func=_cmd_inspect)

    dl = sub.add_parser("delay", help="estimate the flow delay from opposite passes")
    dl.add_argument("config")
    dl.set_defaults(func=_cmd_delay)

    mc = sub.add_parser("machine", help="machine profile: antenna offset, header width, delay")
    mc.add_argument("action", choices=["show", "set", "estimate-offset"])
    mc.add_argument("config")
    mc.add_argument("--machine", help="machine_id (as in the config)")
    mc.add_argument("--antenna-forward", type=float, help="antenna to header centre [m], +fwd")
    mc.add_argument("--antenna-right", type=float, help="antenna to header centre [m], +right")
    mc.add_argument("--header-width", type=float, help="cutting width [m]")
    mc.add_argument("--tau-d", type=float, help="grain flow delay [s]")
    mc.add_argument("--reason", help="why (logged with the change)")
    mc.add_argument("--actor", help="who (default: login name)")
    mc.add_argument("--dry-run", action="store_true")
    mc.set_defaults(func=_cmd_machine)

    tk = sub.add_parser("tickets", help="scale tickets (weighed loads) for reconciliation")
    tk.add_argument("action", choices=["list", "add", "import", "remove"])
    tk.add_argument("config")
    tk.add_argument("file", nargs="?", help="import: weighbridge/dryer CSV or Excel")
    tk.add_argument("--mass-kg", type=float)
    tk.add_argument("--mass-t", type=float)
    tk.add_argument("--moisture", type=float, help="moisture [%%] if the weight is wet")
    tk.add_argument("--machine", help="machine_id that harvested the load")
    tk.add_argument("--share", type=float, help="share of the load from this field (0-1)")
    tk.add_argument("--ref", help="ticket number (duplicates are refused)")
    tk.add_argument("--date")
    tk.add_argument("--note")
    tk.add_argument("--map", action="append", help="import: column map, e.g. mass_kg=Netto")
    tk.add_argument("--unit", choices=["kg", "t"], default="kg", help="import: mass unit")
    tk.add_argument("--reason")
    tk.add_argument("--actor")
    tk.set_defaults(func=_cmd_tickets)

    s2 = sub.add_parser("s2-search", help="list Sentinel-2 scenes over the field (STAC)")
    s2.add_argument("config")
    s2.add_argument("--l4", help="L4 raster to locate the field without boundary/location")
    s2.add_argument("--start")
    s2.add_argument("--end")
    s2.add_argument("--max-cloud", type=float, default=60.0)
    s2.set_defaults(func=_cmd_s2_search)

    z = sub.add_parser("zones", help="management zones from multi-year yield maps (KM-sPC)")
    z.add_argument("layers", nargs="+", help="L4/L5 GeoTIFFs, one per year, same grid origin")
    z.add_argument("--covariate", action="append", help="extra raster (elevation, ECa, ...)")
    z.add_argument("--out", default="zones")
    z.add_argument("--stem", default="zones")
    z.add_argument("--k-max", type=int, default=6)
    z.add_argument("--crs", help="CRS if the rasters do not carry one")
    z.set_defaults(func=_cmd_zones)

    cv = sub.add_parser("covariate", help="covariates from installed plugins (e.g. OmaMaatu)")
    cv.add_argument("action", choices=["list", "fetch"])
    cv.add_argument("name", nargs="?", help="plugin name (see: yieldsuite covariate list)")
    cv.add_argument("config", nargs="?", help="field-year config (boundary and grid)")
    cv.add_argument("--grid", help="raster whose grid to use (e.g. the L4 GeoTIFF)")
    cv.add_argument("--out", help="folder (default: covariates/ next to the output folder)")
    cv.set_defaults(func=_cmd_covariate)

    pl = sub.add_parser("planet", help="PlanetScope: search, order, status, download")
    pl.add_argument("action", choices=["search", "order", "status", "wait", "download"])
    pl.add_argument("config", nargs="?", help="field-year config (search, order)")
    pl.add_argument("--day", help="acquisition day (default: day before the first harvest)")
    pl.add_argument("--days-before", type=int, default=1)
    pl.add_argument("--items", help="item ids to order, comma-separated")
    pl.add_argument("--name", help="order name")
    pl.add_argument("--confirm", action="store_true", help="place the order (uses quota)")
    pl.add_argument("--order-id")
    pl.add_argument("--out", help="download folder")
    pl.set_defaults(func=_cmd_planet)

    rp = sub.add_parser("report", help="level-by-level report: figures, process, audit")
    rp.add_argument("config")
    rp.add_argument("--out", help="folder (default: <output_dir>/report)")
    rp.add_argument("--lang", default="fi", choices=["fi", "en"])
    rp.add_argument("--notes", help="text file with the analyst's findings (shown first)")
    rp.add_argument("--title", help="report title")
    rp.add_argument("--redact", action="store_true", help="file names instead of paths (to share)")
    rp.set_defaults(func=_cmd_report)

    vf = sub.add_parser("verify", help="check a run manifest against the files (SHA-256)")
    vf.add_argument("manifest", help="<field>_<year>_run.json")
    vf.set_defaults(func=_cmd_verify)

    nb = sub.add_parser("nbalance", help="nitrogen removal and balance per management zone")
    nb.add_argument("--zones", required=True, help="zone GeoTIFF from yieldsuite zones")
    nb.add_argument("--yield-map", required=True, help="L4/L5 GeoTIFF of the season")
    nb.add_argument("--quality", help="Q4 GeoTIFF (measured protein / N uptake)")
    nb.add_argument("--grain-n-pct", type=float, help="grain N, %% of dry matter (protein / 6.25)")
    nb.add_argument("--moisture", type=float, default=14.0, help="moisture of the yield map, %%")
    nb.add_argument("--applied-n", help="N applied, kg/ha: one value or per zone (1=110,2=90)")
    nb.add_argument("--out", default="nbalance")
    nb.add_argument("--stem", default="nbalance")
    nb.set_defaults(func=_cmd_nbalance)

    rx = sub.add_parser("prescription", help="variable-rate prescription by zone")
    rx.add_argument("--zones", required=True, help="zone GeoTIFF from yieldsuite zones")
    rx.add_argument("--rates", help="rate per zone in kg/ha, e.g. 1=120,2=100,3=80")
    rx.add_argument("--from-nbalance", help="JSON from yieldsuite nbalance: rates follow removal")
    rx.add_argument("--mean-rate", type=float, help="field mean rate, kg/ha (with --from-nbalance)")
    rx.add_argument("--min-rate", type=float)
    rx.add_argument("--max-rate", type=float)
    rx.add_argument("--product", required=True, help="product name, e.g. 'YaraMila 27-3-3'")
    rx.add_argument("--boundary", help="field boundary (needed for ISOXML)")
    rx.add_argument("--format", default="gpkg,shp", help="gpkg, shp, isoxml (comma-separated)")
    rx.add_argument("--default-rate", type=float, default=0.0, help="rate outside zones, kg/ha")
    rx.add_argument("--task-name", default="Variable rate")
    rx.add_argument("--field-name")
    rx.add_argument("--crs", help="CRS if the zone raster does not carry one")
    rx.add_argument("--out", default="prescription")
    rx.add_argument("--stem", default="prescription")
    rx.set_defaults(func=_cmd_prescription)

    gs = sub.add_parser("grainsense", help="list or export GrainSense Flow (combine) sessions")
    gs.add_argument("action", choices=["sessions", "export"])
    gs.add_argument("--start", help="ISO time, e.g. 2026-08-01T00:00:00Z")
    gs.add_argument("--end")
    gs.add_argument("--uuid", action="append", help="session uuid (repeat)")
    gs.add_argument("--format", default="xlsx", choices=["xlsx", "csv"])
    gs.add_argument("--out")
    gs.add_argument("--base", help="API base URL (default commercial.grainsense.com/api)")
    gs.set_defaults(func=_cmd_grainsense)

    wo = sub.add_parser("webodm", help="process drone flights with WebODM")
    wo.add_argument("action", choices=["submit", "status", "download"])
    wo.add_argument("--url", help="WebODM URL (default $WEBODM_URL)")
    wo.add_argument("--images", help="flight folder (submit)")
    wo.add_argument(
        "--all-images",
        action="store_true",
        help="upload all images, not only Mavic 3M multispectral frames",
    )
    wo.add_argument("--project", default="yieldsuite", help="project name (submit)")
    wo.add_argument("--name", help="task name (submit)")
    wo.add_argument("--project-id", type=int)
    wo.add_argument("--task")
    wo.add_argument("--asset", default="orthophoto.tif")
    wo.add_argument("--out", default="orthophoto.tif")
    wo.set_defaults(func=_cmd_webodm)

    pc = sub.add_parser("parcel", help="field boundary by parcel number (parcel register WFS)")
    pc.add_argument("ids", nargs="+", help="parcel numbers (peruslohkotunnus)")
    pc.add_argument("--year", type=int, help="register year (default: latest layer)")
    pc.add_argument("--out", default="boundary.gpkg")
    pc.add_argument("--url", help="WFS URL (default: Ruokavirasto INSPIRE WFS)")
    pc.add_argument("--layer", help="feature type, if discovery picks the wrong one")
    pc.add_argument("--id-column", help="parcel id attribute, if discovery picks the wrong one")
    pc.set_defaults(func=_cmd_parcel)

    te = sub.add_parser("terrain", help="terrain covariates (elevation, slope, TPI, TWI)")
    te.add_argument(
        "dem",
        nargs="?",
        help="elevation model GeoTIFF (projected CRS, metres); "
        "omit to fetch the NLS 2 m DEM (Finland, needs NLS_API_KEY)",
    )
    te.add_argument("--like", required=True, help="L4/L5 raster whose grid to use")
    te.add_argument("--out", default="terrain.tif")
    te.add_argument("--buffer", type=float, default=200.0, help="TWI catchment buffer [m]")
    te.add_argument("--crs")
    te.set_defaults(func=_cmd_terrain)

    w = sub.add_parser("weather", help="field weather (ERA5 via Open-Meteo) and agroclimate")
    w.add_argument("config")
    w.add_argument("--l4", help="L4 raster to locate the field when config has no location")
    w.add_argument("--refresh", action="store_true", help="fetch again even if cached")
    w.set_defaults(func=_cmd_weather)

    m = sub.add_parser("mcp", help="run the farm-data MCP server (stdio)")
    m.add_argument("--store", help="data store root (default: $YIELDSUITE_STORE)")
    m.set_defaults(func=_cmd_mcp)

    sd = sub.add_parser("store-demo", help="create a data store with a synthetic field")
    sd.add_argument("root")
    sd.set_defaults(func=_cmd_store_demo)

    n = sub.add_parser("notify-test", help="post a test message to Slack")
    n.add_argument("config", nargs="?")
    n.set_defaults(func=_cmd_notify_test)
    return p


def _zone_layer(path):
    from .l4.io import read_bands

    b, g, crs = read_bands(path, require_yield=False)
    if "zone" not in b:
        raise ValueError(f"{path}: no band described as 'zone' (run yieldsuite zones)")
    return b["zone"], g, crs


def _cmd_nbalance(args) -> int:
    import json

    from .l4.io import read_bands
    from .prescription import n_removal, parse_rates, zone_table
    from .zones import place

    try:
        zones, zgrid, crs = _zone_layer(args.zones)
        yb, yg, _ = read_bands(args.yield_map)
        y = place(yb["yield"], yg, zgrid)
        uptake = protein = None
        if args.quality:
            qb, qg, _ = read_bands(args.quality, require_yield=False)
            if "n_uptake_kg_ha" in qb:
                uptake = place(qb["n_uptake_kg_ha"], qg, zgrid)
            if "protein_pct_dm" in qb:
                protein = place(qb["protein_pct_dm"], qg, zgrid)
        removed, how = n_removal(
            y,
            n_uptake_kg_ha=uptake,
            grain_n_pct_dm=args.grain_n_pct,
            moisture_pct=args.moisture,
        )
        applied = None
        if args.applied_n:
            applied = (
                parse_rates(args.applied_n) if "=" in args.applied_n else float(args.applied_n)
            )
        table = zone_table(
            zones,
            zgrid,
            y,
            n_removed_kg_ha=removed,
            protein_pct_dm=protein,
            applied_n_kg_ha=applied,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    table.notes.append(f"N removal from {how}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{args.stem}.json"
    p.write_text(json.dumps(table.as_dict(), indent=2))
    print(f"N removal from {how}")
    for r in table.rows:
        nan = float("nan")
        line = (
            f"  zone {r['zone']}: {r['area_ha']:.2f} ha, "
            f"yield {r['yield_t_ha'] or nan:.2f} t/ha, "
            f"N removed {r.get('n_removed_kg_ha') or nan:.0f} kg/ha"
        )
        if "n_balance_kg_ha" in r:
            line += f", balance {r['n_balance_kg_ha']:+.0f} kg/ha"
        print(line)
    print(f"wrote {p}")
    return 0


def _cmd_prescription(args) -> int:
    import json

    from .prescription import (
        ZoneTable,
        parse_rates,
        prescription_polygons,
        rates_from_removal,
        write_prescription,
    )

    try:
        zones, zgrid, crs = _zone_layer(args.zones)
        crs = args.crs or crs
        meta = {}
        if args.rates:
            rates = parse_rates(args.rates)
            meta["rates_from"] = "given per zone"
        elif args.from_nbalance:
            if args.mean_rate is None:
                raise ValueError("--from-nbalance needs --mean-rate")
            table = ZoneTable(json.loads(Path(args.from_nbalance).read_text())["zones"])
            rates = rates_from_removal(table, args.mean_rate, args.min_rate, args.max_rate)
            meta["rates_from"] = (
                f"proportional to N removal, mean {args.mean_rate} kg/ha"
                f", limits {args.min_rate}-{args.max_rate}"
            )
        else:
            raise ValueError("give --rates or --from-nbalance")
        presc = prescription_polygons(zones, zgrid, crs, rates, args.product)
        boundary = None
        if args.boundary:
            from .public.boundary import load_boundary

            boundary = load_boundary(args.boundary, crs)
        formats = [f.strip() for f in args.format.split(",") if f.strip()]
        files = write_prescription(
            presc,
            args.out,
            args.stem,
            formats,
            boundary=boundary,
            task_name=args.task_name,
            field_name=args.field_name or args.stem,
            product=args.product,
            default_rate=args.default_rate,
            meta=meta,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    s = json.loads(Path(files["summary"]).read_text())
    for z in s["zones"]:
        print(f"  zone {z['zone']}: {z['rate_kg_ha']:.0f} kg/ha on {z['area_ha']:.2f} ha")
    print(f"mean {s['mean_rate_kg_ha']:.0f} kg/ha, total {s['total_kg']:.0f} kg of {args.product}")
    for k, v in files.items():
        print(f"  {k:8} {v}")
    print("Check the file on the target terminal before use.")
    return 0


def _cmd_report(args) -> int:
    from .report import build_report

    cfg = load_config(args.config)
    try:
        notes = Path(args.notes).read_text(encoding="utf-8") if args.notes else None
        files = build_report(
            cfg, args.out, lang=args.lang, notes=notes, title=args.title, redact=args.redact
        )
    except (FileNotFoundError, ValueError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"report   {files['html']}")
    print(f"process  {files['process']}")
    print(f"audit    {files['audit']}")
    for level, path in files["figures"].items():
        print(f"  {level:4} {path}")
    return 0


def _cmd_verify(args) -> int:
    from .audit import verify_manifest

    problems = verify_manifest(args.manifest)
    if problems:
        for p in problems:
            print(p)
        return 1
    print("all inputs and outputs unchanged since the run")
    return 0


def _cmd_covariate(args) -> int:
    from . import plugins

    if args.action == "list":
        found = plugins.available()
        if not found:
            print("no covariate plugins installed (entry-point group yieldsuite.covariates)")
        for p in found:
            print(f"  {p.name:14} {p.target}  ({p.distribution or '?'})")
        return 0
    if not args.name or not args.config:
        print("error: covariate fetch needs NAME and CONFIG", file=sys.stderr)
        return 1
    cfg = load_config(args.config)
    if cfg.boundary is None:
        print("error: the config needs a boundary (yieldsuite parcel <id>)", file=sys.stderr)
        return 1
    from .public.boundary import load_boundary

    try:
        boundary = load_boundary(cfg.boundary, cfg.crs)
        if args.grid:
            from .l4.io import read_bands

            _, grid, _ = read_bands(args.grid, require_yield=False)
        else:
            from .l4.grid import Grid

            grid = Grid.from_bounds(boundary.bounds, cfg.grid_resolution_m, cfg.grid_origin)
        provider = plugins.load(args.name)
        layers = plugins.fetch(provider, boundary, cfg.crs)
        out = args.out or str(Path(cfg.output_dir).parent / "covariates")
        files = plugins.write_covariates(provider, layers, grid, cfg.crs, out)
    except (plugins.PluginError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{provider.name} {getattr(provider, 'version', '')}: {', '.join(files['bands'])}")
    print(f"  raster      {files['raster']}")
    print(f"  provenance  {files['provenance']}")
    print(f"  licence     {provider.licence}")
    for n in layers.notes:
        print(f"  note        {n}")
    return 0


def _field_geojson(cfg, buffer_m: float = 0.0) -> dict:
    import json as _json

    from .public.boundary import load_boundary

    if cfg.boundary is None:
        raise ValueError("the config needs a boundary (yieldsuite parcel <id>)")
    geom = load_boundary(cfg.boundary, cfg.crs)
    if buffer_m:
        geom = geom.buffer(buffer_m)
    import geopandas as gpd

    return _json.loads(gpd.GeoSeries([geom], crs=cfg.crs).to_crs(4326).to_json())["features"][0][
        "geometry"
    ]


def _cmd_planet(args) -> int:
    import json as _json
    from datetime import date as _date
    from datetime import timedelta

    from .public import planet

    try:
        if args.action in ("status", "wait", "download"):
            if not args.order_id:
                raise ValueError(f"planet {args.action} needs --order-id")
            if args.action == "download":
                files = planet.download(args.order_id, args.out or "planet")
                print(f"{len(files)} files -> {args.out or 'planet'} ({planet.LICENCE})")
                return 0
            st = (
                planet.wait(args.order_id)
                if args.action == "wait"
                else planet.status(args.order_id)
            )
            st.pop("_results", None)
            print(_json.dumps(st, indent=2))
            return 0
        cfg = load_config(args.config)
        if args.action == "search":
            if args.day:
                day = _date.fromisoformat(args.day)
            else:
                import pandas as pd

                rec = Path(cfg.output_dir) / f"L3_{cfg.field_id}_{cfg.year}_records.parquet"
                if not rec.exists():
                    raise ValueError("give --day, or run the chain first (harvest date from L3)")
                t = pd.read_parquet(rec, columns=["t", "header_down"])
                t = t[t["header_down"]]["t"].min()
                first = pd.to_datetime(t, unit="s", utc=True).tz_convert("Europe/Helsinki").date()
                day = first - timedelta(days=args.days_before)
                print(f"first harvest day {first}; searching {day}")
            rows = planet.candidates(_field_geojson(cfg), day)
            for r in rows:
                print(
                    f"  {r['id']}  {str(r['acquired'])[11:19]}  parcel clear "
                    f"{r['parcel_clear_pct']}  scene {r['scene_clear_pct']}  {r['instrument']}  "
                    f"{r['quality']}{'  SR' if r['has_sr'] else ''}"
                )
            if not rows:
                print("no PlanetScope scene over the field on that day")
            return 0
        if args.action == "order":
            if not args.items:
                raise ValueError("planet order needs --items id1,id2")
            if not args.confirm:
                print("an order uses Planet quota (clipped area); add --confirm to place it")
                return 1
            oid = planet.order(
                [i.strip() for i in args.items.split(",") if i.strip()],
                _field_geojson(cfg, buffer_m=10.0),
                args.name or f"{cfg.field_id} {cfg.year}",
            )
            print(f"order {oid} placed; follow with: yieldsuite planet wait --order-id {oid}")
            return 0
    except (planet.PlanetError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


def _prog_name() -> str:
    """The command as invoked; 'python -m' shows yieldsuite."""
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name == "yieldsuite" else "yieldsuite"


def main(argv: list[str] | None = None) -> int:
    from .inputs import InputError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
