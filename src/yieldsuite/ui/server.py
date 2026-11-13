"""Local browser UI (``yieldsuite ui``): fields, certainty, maps, inputs, runs.

Standard library only. The server binds to 127.0.0.1 by default and is meant for one
person on their own machine: there are no accounts. Every change (POST) needs the token
that the page receives when it is loaded, so another web page open in the same browser
cannot post on the user's behalf. Files are served only from the fields' output folders.

Routes
    GET  /                          the page
    GET  /api/fields                fields with product grades
    GET  /api/field/<key>           summary, certainty, inputs, files, run state
    POST /api/field/<key>/ticket    add a scale ticket
    POST /api/field/<key>/machine   antenna position / header width
    POST /api/field/<key>/setting   light_use.fill, crop.sowing_date ...
    POST /api/field/<key>/run       run the chain (and report, visualisations) in the background
    GET  /files/<key>/<path>        a file from the field's output folder
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PAGE = Path(__file__).parent / "index.html"
FIGURES = [("L4_grid.png", "L4"), ("L5.png", "L5"), ("moisture.png", "moisture"),
           ("Q4.png", "quality"), ("review.png", "review"), ("harvest_check.png", "harvest"),
           ("moisture_time.png", "moisture_time"), ("season.png", "season")]


def find_configs(paths) -> dict[str, Path]:
    """Field configs under the given files or folders, keyed by a URL-safe name."""
    found: dict[str, Path] = {}
    for p in map(Path, paths):
        cands = (
            [p] if p.is_file() else sorted(p.glob("field.yaml")) + sorted(p.glob("*/field.yaml"))
        )
        for c in cands:
            key = c.parent.name or "field"
            n, k = 2, key
            while k in found and found[k] != c.resolve():
                k, n = f"{key}-{n}", n + 1
            found[k] = c.resolve()
    return found


class App:
    def __init__(self, configs: dict[str, Path], redact: bool = True):
        self.configs = configs
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, dict] = {}
        self.redact = redact
        self.lock = threading.Lock()

    # --- reading ------------------------------------------------------------------
    def _cfg(self, key):
        from ..config import load_config

        return load_config(self.configs[key])

    def _json(self, path: Path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def fields(self) -> list[dict]:
        from .. import certainty

        out = []
        for key, path in self.configs.items():
            row = {"key": key, "config": path.name, "job": self._job_state(key)}
            try:
                cfg = self._cfg(key)
                row.update(field_id=str(cfg.field_id), year=cfg.year)
                base = f"{cfg.field_id}_{cfg.year}"
                o = Path(cfg.output_dir)
                row["has_outputs"] = (o / f"L4_{base}_qa.json").exists()
                if row["has_outputs"]:
                    res = self._json(o / f"{base}_certainty.json") or certainty.assess(cfg)
                    row["products"] = {k: v["certainty"] for k, v in res["products"].items()}
                    row["questions"] = len(res["questions"])
            except Exception as exc:  # noqa: BLE001 - one broken config must not hide the rest
                row["error"] = f"{type(exc).__name__}: {exc}"
            out.append(row)
        return out

    def field(self, key: str) -> dict:
        from .. import certainty, inputs

        cfg = self._cfg(key)
        base = f"{cfg.field_id}_{cfg.year}"
        o = Path(cfg.output_dir)
        qa = self._json(o / f"L4_{base}_qa.json") or {}
        q4 = self._json(o / f"Q4_{base}_qa.json") or {}
        card = self._json(o / f"L5_{base}_model_card.json") or {}
        man = self._json(o / f"{base}_run.json") or {}
        lu = card.get("light_use") or {}
        res = certainty.assess(cfg) if qa else None
        files = {}
        for name, rel in (
            ("report", f"report/{base}_report.html"),
            ("viewer3d", f"viz/{base}_harvest_3d.html"),
            ("timelapse", f"viz/{base}_timelapse.mp4"),
            ("gapfill3d", f"viz/{base}_gapfill_3d.html"),
            ("gapfill", f"viz/{base}_gapfill.mp4"),
            ("certainty", f"{base}_certainty.json"),
            ("manifest", f"{base}_run.json"),
            ("model_card", f"L5_{base}_model_card.json"),
            ("L4", f"L4_{base}.tif"),
            ("L5", f"L5_{base}.tif"),
            ("records", f"L3_{base}_records.parquet"),
        ):
            if (o / rel).exists():
                files[name] = rel
        figs = [{"name": n, "path": f"report/figures/{f}"} for f, n in FIGURES
                if (o / "report" / "figures" / f).exists()]
        tickets = inputs.read_tickets(self.configs[key])
        mm = qa.get("moisture_map") or {}
        return {
            "key": key,
            "field_id": str(cfg.field_id),
            "year": cfg.year,
            "has_outputs": bool(qa),
            "kpi": {
                "yield_t_ha": qa.get("field_mean_yield_t_ha"),
                "mass_t": (qa.get("reconciled_mass_kg") or 0) / 1000 or None,
                "moisture_pct": mm.get("field_mean_pct"),
                "protein_pct": q4.get("field_mean_protein_pct_dm"),
                "filled_share": (card.get("cells") or {}).get("filled_share"),
                "product_class": qa.get("product_class"),
                "finished": man.get("finished_utc"),
                "code_version": (man.get("software") or {}).get("version"),
            },
            "certainty": res,
            "machines": [
                {"machine_id": m, "antenna_offset_m": list(p.antenna_offset_m),
                 "header_width_m": p.header_width_m}
                for m, p in cfg.machines.items()
            ],
            "tickets": json.loads(tickets.to_json(orient="records")) if len(tickets) else [],
            "model": {
                "used": card.get("prediction_model"),
                "setting": cfg.light_use.fill,
                "clear_winner": lu.get("clear_winner"),
                "reason": lu.get("fill_reason") or card.get("prediction_note"),
                "candidates": {k: v.get("cv_rmse") for k, v in
                               (lu.get("fill_candidates") or {}).items()},
            },
            "crop": {"sowing_date": cfg.crop.sowing_date if cfg.crop else None,
                     "assumed": bool(getattr(cfg.crop, "sowing_date_assumed", False))
                     if cfg.crop else False},
            "notes": (man.get("result") or {}).get("notes") or [],
            "files": files,
            "figures": figs,
            "log": inputs.read_log(self.configs[key])[-12:],
            "job": self._job_state(key),
        }

    # --- changes ------------------------------------------------------------------
    def add_ticket(self, key, body):
        from .. import inputs

        row = {k: body.get(k) for k in ("mass_kg", "machine_id", "moisture_pct", "field_share",
                                        "ticket_ref", "date", "note")}
        row = {k: (None if v in ("", None) else v) for k, v in row.items()}
        inputs.add_tickets(self.configs[key], [row], actor="ui", reason=body.get("reason") or "ui")
        return {"ok": True}

    def set_machine(self, key, body):
        from .. import inputs

        def num(k):
            v = body.get(k)
            return None if v in ("", None) else float(v)

        ch = inputs.set_machine(
            self.configs[key], str(body.get("machine_id") or ""),
            antenna_forward_m=num("antenna_forward_m"), antenna_right_m=num("antenna_right_m"),
            header_width_m=num("header_width_m"), reason=body.get("reason") or "", actor="ui")
        return {"ok": True, **ch.as_dict()}

    def set_setting(self, key, body):
        from .. import inputs

        res = inputs.set_setting(self.configs[key], body.get("key"), body.get("value"),
                                 body.get("reason") or "ui", actor="ui")
        if body.get("key") == "crop.sowing_date":  # a date the user enters is a record
            inputs.set_setting(self.configs[key], "crop.sowing_date_assumed", False, "ui", "ui")
        return {"ok": True, **res}

    # --- runs ---------------------------------------------------------------------
    def _job_state(self, key):
        j = self.jobs.get(key)
        if not j:
            return None
        return {"state": j["state"], "step": j["step"], "started": j["started"],
                "log": j["log"][-40:], "returncode": j.get("returncode")}

    def run(self, key, body):
        with self.lock:
            j = self.jobs.get(key)
            if j and j["state"] == "running":
                raise ValueError("a run is already in progress for this field")
            j = self.jobs[key] = {"state": "running", "step": "", "log": [],
                                  "started": time.strftime("%H:%M:%S")}
        cfg = str(self.configs[key])
        steps = [("chain", ["run", cfg, "--to", str(body.get("to") or "L5")]),
                 ("report", ["report", cfg] + (["--redact"] if self.redact else []))]
        if body.get("visuals"):
            steps += [("timelapse", ["timelapse", cfg]), ("gapfill", ["gapfill-story", cfg])]

        def work():
            rc = 0
            for name, args in steps:
                j["step"] = name
                j["log"].append(f"$ yieldsuite {' '.join(args[:1])} …")
                proc = subprocess.Popen(  # noqa: S603 - our own module, fixed arguments
                    [sys.executable, "-m", "yieldsuite.cli", *args], cwd=Path(cfg).parent,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    j["log"].append(line.rstrip()[:300])
                    del j["log"][:-200]
                rc = proc.wait()
                if rc != 0:
                    break
            j.update(state="done" if rc == 0 else "failed", returncode=rc)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def file_path(self, key, rel) -> Path | None:
        cfg = self._cfg(key)
        root = Path(cfg.output_dir).resolve()
        p = (root / rel).resolve()
        return p if p.is_file() and root in p.parents else None


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "yieldsuite-ui"

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, ensure_ascii=False, default=str).encode()
                if not isinstance(body, str) else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _key(self, parts):
            key = unquote(parts[0]) if parts else ""
            if key not in app.configs:
                self._send(404, {"error": "unknown field"})
                return None
            return key

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                    return self._send(200, html, "text/html; charset=utf-8")
                if path == "/api/fields":
                    return self._send(200, {"fields": app.fields()})
                if path.startswith("/api/field/"):
                    key = self._key(path.split("/")[3:4])
                    return key and self._send(200, app.field(key))
                if path.startswith("/files/"):
                    parts = path.split("/", 3)
                    key = self._key(parts[2:3])
                    if not key:
                        return None
                    f = app.file_path(key, unquote(parts[3]) if len(parts) > 3 else "")
                    if f is None:
                        return self._send(404, {"error": "no such file"})
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype)
                return self._send(404, {"error": "not found"})
            except Exception as exc:  # noqa: BLE001 - shown to the user
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):  # noqa: N802
            if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                return self._send(403, {"error": "missing or wrong token: reload the page"})
            parts = urlparse(self.path).path.split("/")
            if len(parts) != 5 or parts[1:3] != ["api", "field"]:
                return self._send(404, {"error": "not found"})
            key = self._key(parts[3:4])
            if not key:
                return None
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n < 100_000 else {}
                action = {"ticket": app.add_ticket, "machine": app.set_machine,
                          "setting": app.set_setting, "run": app.run}.get(parts[4])
                if action is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, action(key, body))
            except Exception as exc:  # noqa: BLE001 - validation errors go back to the form
                return self._send(400, {"error": str(exc)})

    return Handler


def serve(paths, host: str = "127.0.0.1", port: int = 8765, redact: bool = True):
    """Create the server (not started); returns (server, app)."""
    configs = find_configs(paths)
    if not configs:
        raise ValueError("no field.yaml found in the given paths")
    app = App(configs, redact)
    return ThreadingHTTPServer((host, port), make_handler(app)), app
