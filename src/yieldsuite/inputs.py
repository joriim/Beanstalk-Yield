"""Entering machine settings and scale tickets for a field-year config.

Two inputs change the result more than extra imagery does:

- **antenna position** in the machine profile (``antenna_offset_m``: forward and
  right from the GNSS antenna to the header centre). A lateral offset left out
  shifts every pass sideways and shows up as a level difference between passes
  driven in opposite directions;
- **scale tickets** (weighbridge, grain-cart scale, dryer intake), which
  reconcile the logged mass per machine (L3) and turn the product class from
  ``absolute_unreconciled`` into ``absolute``.

The field YAML is edited in place line by line so that comments survive; the
result is parsed again and the write is refused if it does not read back as
intended. Every change is appended to ``inputs_log.jsonl`` next to the config
with the old and the new value, who made it and why.
"""

from __future__ import annotations

import getpass
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

LOG_NAME = "inputs_log.jsonl"
TICKETS_NAME = "scale_tickets.csv"
TICKET_COLUMNS = [
    "ticket_ref",
    "date",
    "machine_id",
    "mass_kg",
    "moisture_pct",
    "field_share",
    "note",
    "added",
    "actor",
]
MACHINE_KEYS = ("antenna_offset_m", "header_width_m", "tau_d_s", "tau_m_s", "k_factor")


class InputError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _actor(actor: str | None) -> str:
    if actor:
        return actor
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user database in some containers
        return "unknown"


def log_change(config_path: Path, action: str, actor: str, reason: str, **detail) -> dict:
    entry = {"time": _now(), "actor": actor, "action": action, "reason": reason, **detail}
    with open(Path(config_path).parent / LOG_NAME, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def read_log(config_path: str | Path) -> list[dict]:
    p = Path(config_path).parent / LOG_NAME
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# --- YAML text editing ------------------------------------------------------------


def _roundtrip():
    """YAML reader/writer that keeps comments, key order and layout."""
    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _dump(y, data) -> str:
    import io

    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _flow(values):
    from ruamel.yaml.comments import CommentedSeq

    seq = CommentedSeq(values)
    seq.fa.set_flow_style()
    return seq


def set_machine_values(text: str, machine_id: str, values: dict) -> str:
    """Return ``text`` with ``values`` set in the profile of ``machine_id``.

    A missing profile is appended to ``machines:`` (created if absent). Comments and
    the order of keys are kept, wherever ``machine_id`` stands in the entry.
    """
    y = _roundtrip()
    data = y.load(text) or {}
    machines = data.get("machines")
    if machines is None:
        from ruamel.yaml.comments import CommentedSeq

        machines = data["machines"] = CommentedSeq()
    entry = next((m for m in machines if str(m.get("machine_id")) == str(machine_id)), None)
    if entry is None:
        from ruamel.yaml.comments import CommentedMap

        entry = CommentedMap(machine_id=machine_id, version=1)
        machines.append(entry)
    for key, value in values.items():
        entry[key] = _flow(value) if isinstance(value, (list, tuple)) else value
    return _dump(y, data)


def set_top_value(text: str, key: str, value, comment: str = "") -> str:
    y = _roundtrip()
    data = y.load(text) or {}
    new = key not in data
    data[key] = value
    if new and comment:
        data.yaml_add_eol_comment(comment, key)
    return _dump(y, data)


def _machines(raw: dict) -> dict[str, dict]:
    return {str(m.get("machine_id")): m for m in (raw.get("machines") or [])}


@dataclass
class MachineChange:
    machine_id: str
    old: dict
    new: dict

    def as_dict(self) -> dict:
        return {"machine_id": self.machine_id, "old": self.old, "new": self.new}


def set_machine(
    config_path: str | Path,
    machine_id: str,
    antenna_forward_m: float | None = None,
    antenna_right_m: float | None = None,
    header_width_m: float | None = None,
    tau_d_s: float | None = None,
    reason: str = "",
    actor: str | None = None,
    dry_run: bool = False,
) -> MachineChange:
    """Set antenna offset / header width / delay of one machine in the field YAML."""
    path = Path(config_path)
    if not reason:
        raise InputError("give a reason (e.g. 'measured with a tape, antenna 0.6 m left')")
    text = path.read_text()
    raw = yaml.safe_load(text) or {}
    old = dict(_machines(raw).get(str(machine_id), {}))
    old_off = [float(v) for v in old.get("antenna_offset_m", [0.0, 0.0])]
    values: dict = {}
    if antenna_forward_m is not None or antenna_right_m is not None:
        values["antenna_offset_m"] = [
            float(antenna_forward_m) if antenna_forward_m is not None else old_off[0],
            float(antenna_right_m) if antenna_right_m is not None else old_off[1],
        ]
        for v in values["antenna_offset_m"]:
            if abs(v) > 15:
                raise InputError(f"antenna offset {v} m is implausible (|offset| > 15 m)")
    if header_width_m is not None:
        if not 1 <= header_width_m <= 20:
            raise InputError("header width must be 1-20 m")
        values["header_width_m"] = float(header_width_m)
    if tau_d_s is not None:
        if not 0 <= tau_d_s <= 60:
            raise InputError("tau_d_s must be 0-60 s")
        values["tau_d_s"] = float(tau_d_s)
    if not values:
        raise InputError("nothing to change: give an antenna offset, header width or tau_d")
    new_text = set_machine_values(text, machine_id, values)
    check = _machines(yaml.safe_load(new_text) or {}).get(str(machine_id), {})
    for k, v in values.items():
        got = check.get(k)
        got = [float(x) for x in got] if isinstance(got, list) else got
        if got != v:
            raise InputError(f"edit did not read back ({k}: {got!r} != {v!r}); file unchanged")
    before = {k: old.get(k) for k in values}
    if not dry_run:
        path.write_text(new_text)
        log_change(
            path,
            "set_machine",
            _actor(actor),
            reason,
            machine_id=machine_id,
            old=before,
            new=values,
        )
    return MachineChange(str(machine_id), before, values)


# --- scale tickets ------------------------------------------------------------------


def tickets_path(config_path: str | Path, create: bool = False) -> Path:
    """Tickets CSV of the config; with ``create`` the config is pointed to a new one."""
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    if raw.get("scale_tickets"):
        p = Path(raw["scale_tickets"])
        return p if p.is_absolute() else path.parent / p
    p = path.parent / TICKETS_NAME
    if create:
        path.write_text(
            set_top_value(
                path.read_text(),
                "scale_tickets",
                TICKETS_NAME,
                "weighed loads (yieldsuite tickets)",
            )
        )
    return p


def read_tickets(config_path: str | Path) -> pd.DataFrame:
    p = tickets_path(config_path)
    if not p.exists():
        return pd.DataFrame(columns=TICKET_COLUMNS)
    df = pd.read_csv(p, dtype={"ticket_ref": str, "machine_id": str})
    return df.reindex(columns=list(df.columns) + [c for c in TICKET_COLUMNS if c not in df])


def _write_rows(p: Path, rows: list[dict]) -> None:
    """Append rows; an older file without some of our columns gets them (empty)."""
    new = pd.DataFrame(rows)
    if p.exists():
        old = pd.read_csv(p, dtype=str)
        if "mass_kg" not in old.columns:
            raise InputError(f"{p.name} lacks a mass_kg column")
        cols = list(old.columns) + [c for c in TICKET_COLUMNS if c not in old.columns]
        df = pd.concat([old.reindex(columns=cols), new.reindex(columns=cols)], ignore_index=True)
    else:
        df = new.reindex(columns=TICKET_COLUMNS)
    df.to_csv(p, index=False)


def _validate_ticket(row: dict, machines: set[str], existing_refs: set[str]) -> dict:
    try:
        mass = float(row["mass_kg"])
    except (KeyError, TypeError, ValueError):
        raise InputError(f"ticket {row.get('ticket_ref') or ''}: mass_kg missing") from None
    if not 0 < mass < 100_000:
        raise InputError(f"ticket {row.get('ticket_ref') or ''}: mass {mass} kg out of range")
    mc = row.get("moisture_pct")
    if mc not in (None, "") and not 0 <= float(mc) < 60:
        raise InputError(f"moisture {mc} % out of range (0-60)")
    share = row.get("field_share")
    if share not in (None, "") and not 0 < float(share) <= 1:
        raise InputError("field_share must be in (0, 1]")
    mid = str(row.get("machine_id") or "*")
    if mid != "*" and machines and mid not in machines:
        raise InputError(f"machine {mid!r} not in the config (machines: {sorted(machines)})")
    ref = str(row.get("ticket_ref") or "")
    if ref and ref in existing_refs:
        raise InputError(f"ticket {ref!r} is already entered")
    return {**row, "machine_id": mid, "mass_kg": mass, "ticket_ref": ref}


def add_tickets(
    config_path: str | Path,
    rows: list[dict],
    actor: str | None = None,
    reason: str = "",
) -> list[dict]:
    """Append tickets (dicts with mass_kg and optional machine_id, moisture_pct,
    field_share, ticket_ref, date, note). All rows are checked before any is written."""
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    machines = set(_machines(raw))
    existing = read_tickets(path)
    refs = set(existing["ticket_ref"].dropna().astype(str)) - {""}
    kinds = set(existing["machine_id"].fillna("*").astype(str)) if len(existing) else set()
    who, now = _actor(actor), _now()
    out = []
    for r in rows:
        v = _validate_ticket(r, machines, refs)
        if v["ticket_ref"]:
            refs.add(v["ticket_ref"])
        kinds.add("*" if v["machine_id"] == "*" else "machine")
        out.append({**v, "added": now, "actor": who})
    if "*" in kinds and len(kinds) > 1:
        raise InputError(
            "tickets would mix machine-specific and unassigned rows; give every ticket "
            "a machine or none of them"
        )
    p = tickets_path(path, create=True)
    _write_rows(p, out)
    log_change(
        path,
        "add_tickets",
        who,
        reason,
        file=p.name,
        tickets=[{k: r.get(k) for k in TICKET_COLUMNS[:7]} for r in out],
    )
    return out


def remove_ticket(config_path: str | Path, ticket_ref: str, reason: str, actor=None) -> dict:
    """Remove one ticket by reference; the removed row is kept in the log."""
    if not reason:
        raise InputError("give a reason for removing a ticket")
    path = Path(config_path)
    p = tickets_path(path)
    df = read_tickets(path)
    hit = df["ticket_ref"].astype(str) == str(ticket_ref)
    if not hit.any():
        raise InputError(f"no ticket {ticket_ref!r}")
    removed = df[hit].iloc[0].to_dict()
    df[~hit].to_csv(p, index=False)
    log_change(path, "remove_ticket", _actor(actor), reason, removed=removed)
    return removed


def _number(v):
    if isinstance(v, str):
        v = v.strip().replace(" ", "").replace(" ", "")
        if v.count(",") == 1 and "." not in v:
            v = v.replace(",", ".")
    return pd.to_numeric(v, errors="coerce")


def import_tickets(
    config_path: str | Path,
    source: str | Path,
    mapping: dict[str, str] | None = None,
    mass_unit: str = "kg",
    machine_id: str | None = None,
    actor: str | None = None,
    reason: str = "",
) -> list[dict]:
    """Import a weighbridge/dryer export (CSV with ``,`` or ``;``, or Excel).

    ``mapping`` maps our columns to the file's, e.g. {"mass_kg": "Netto",
    "moisture_pct": "Kosteus", "ticket_ref": "Kuitti"}; columns already named
    like ours need no mapping.
    """
    src = Path(source)
    if src.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(src)
    else:
        df = pd.read_csv(src, sep=None, engine="python", dtype=str)
    mapping = dict(mapping or {})
    for ours in ("mass_kg", "moisture_pct", "machine_id", "ticket_ref", "date", "field_share"):
        mapping.setdefault(ours, ours)
    if mapping["mass_kg"] not in df.columns:
        raise InputError(f"no mass column {mapping['mass_kg']!r}; columns: {list(df.columns)}")
    factor = {"kg": 1.0, "t": 1000.0}[mass_unit]
    rows = []
    for _, r in df.iterrows():
        mass = _number(r[mapping["mass_kg"]])
        if not pd.notna(mass):
            continue  # total rows, blank lines
        row = {"mass_kg": float(mass) * factor}
        for ours in ("moisture_pct", "field_share"):
            col = mapping[ours]
            if col in df.columns and pd.notna(_number(r[col])):
                row[ours] = float(_number(r[col]))
        for ours in ("machine_id", "ticket_ref", "date"):
            col = mapping[ours]
            if col in df.columns and pd.notna(r[col]) and str(r[col]).strip():
                row[ours] = str(r[col]).strip()
        if machine_id and not row.get("machine_id"):
            row["machine_id"] = machine_id
        row["note"] = f"imported from {src.name}"
        rows.append(row)
    if not rows:
        raise InputError(f"no tickets with a mass in {src.name}")
    return add_tickets(config_path, rows, actor, reason or f"import {src.name}")


def ticket_summary(config_path: str | Path, reference_pct: float | None = None) -> pd.DataFrame:
    """Per machine: tickets, wet mass and mass at the reference moisture."""
    from .config import load_config
    from .ingest.scale import read_scale_tickets

    cfg = load_config(config_path)
    p = tickets_path(config_path)
    if not p.exists():
        return pd.DataFrame(columns=["machine_id", "tickets", "mass_kg", "mass_ref_kg"])
    ref = cfg.reference_moisture_pct if reference_pct is None else reference_pct
    t = read_scale_tickets(p, ref)
    wet = read_tickets(config_path)["mass_kg"].astype(float).to_numpy()
    t["mass_kg"] = wet
    g = t.groupby("machine_id").agg(
        tickets=("mass_ref_kg", "size"),
        mass_kg=("mass_kg", "sum"),
        mass_ref_kg=("mass_ref_kg", "sum"),
    )
    return g.reset_index()
