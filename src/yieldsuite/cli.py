"""Command-line tool: ``yieldsuite``."""

from __future__ import annotations

import argparse
import sys

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


def _cmd_notify_test(args) -> int:
    from .notify.slack import SlackNotifier

    settings = load_config(args.config).slack if args.config else None
    SlackNotifier(settings or SlackSettings()).send(
        f":seedling: yieldsuite {__version__} can post to this channel"
    )
    print("sent")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yieldsuite", description=__doc__)
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

    n = sub.add_parser("notify-test", help="post a test message to Slack")
    n.add_argument("config", nargs="?")
    n.set_defaults(func=_cmd_notify_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
