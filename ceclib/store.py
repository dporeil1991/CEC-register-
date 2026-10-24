"""SQLite store built from the committed text data.

The *record* is the text in data/ (JSON per category, jobs.csv, aliases.json): it
is diffable and survives in git. data/cec.db is an index rebuilt from those files
whenever they change, so nothing is lost if it is deleted.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from .normalise import brand_short, brands_for_manufacturer, extract_kw, extract_watts, model_key, split_standard

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = Path(os.environ["CEC_DB_PATH"]) if os.environ.get("CEC_DB_PATH") else DATA / "cec.db"
JOBS_CSV = DATA / "jobs.csv"
# Running as an installed skill: the folder may be read-only and there is no jobs log to keep.
SKILL_MODE = os.environ.get("CEC_SKILL") == "1" or (ROOT / "SKILL.md").exists()


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def _db_location(db_path: Path) -> Path:
    """The index lives beside the data when it can, otherwise in the temp directory."""
    if _writable(db_path.parent):
        return db_path
    return Path(tempfile.gettempdir()) / "cec-register" / db_path.name
KINDS = ("inverters", "modules", "batteries")
KIND_ALIASES = {
    "inverter": "inverters", "inverters": "inverters", "inv": "inverters", "pce": "inverters",
    "panel": "modules", "panels": "modules", "module": "modules", "modules": "modules", "pv": "modules",
    "battery": "batteries", "batteries": "batteries", "bat": "batteries", "ess": "batteries",
    "all": None, "any": None,
}
KIND_NOUN = {"inverters": "inverter", "modules": "solar panel", "batteries": "battery"}
KIND_LABEL = {"inverters": "Inverter", "modules": "PV module", "batteries": "Battery"}
SCHEMA_VERSION = 3

JOB_COLUMNS = ["checked_at", "job", "kind", "query", "result", "listed_name", "manufacturer",
               "approved_until", "as_at", "data_pulled", "note"]

SCHEMA = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE pulls (
  kind TEXT PRIMARY KEY, label TEXT, source_url TEXT, source_file TEXT, sha256 TEXT,
  pulled_at TEXT, records INTEGER, current_records INTEGER, field_mapping TEXT
);
CREATE TABLE listings (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, manufacturer TEXT, brand TEXT, series TEXT,
  model_listed TEXT NOT NULL, model_base TEXT, model_key TEXT, standard TEXT,
  rating REAL, rating_unit TEXT, approved_date TEXT, approved_until TEXT,
  first_seen TEXT, last_seen TEXT, delisted_date TEXT, extra TEXT, source_file TEXT, source_pull TEXT
);
CREATE INDEX ix_listings_kind_key ON listings(kind, model_key);
CREATE INDEX ix_listings_kind_man ON listings(kind, manufacturer);
CREATE TABLE jobs (
  rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(c + ' TEXT' for c in JOB_COLUMNS)}
);
"""


def resolve_kind(word: str | None) -> tuple[str | None, bool]:
    """('inverter', True) if word names a category; (None, False) if it is not a category word."""
    if word is None:
        return None, False
    w = word.strip().lower()
    if w in KIND_ALIASES:
        return KIND_ALIASES[w], True
    return None, False


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def _sources() -> list[Path]:
    return [DATA / f"{k}.json" for k in KINDS] + [DATA / "meta.json", JOBS_CSV, DATA / "aliases.json"]


def _fingerprint() -> str:
    h = hashlib.sha256(str(SCHEMA_VERSION).encode())
    for p in _sources():
        h.update(p.name.encode() + _sha256(p).encode())
    return h.hexdigest()


def rating_for(kind: str, rec: dict) -> tuple[float | None, str | None]:
    """(rating, unit) from the source column, or extracted from the model text for panels."""
    label = (rec.get("power_label") or "").lower()
    val = rec.get("power")
    if val is not None:
        if "kwh" in label or kind == "batteries":
            return float(val), "kWh"
        if "kw" in label or kind == "inverters":
            return float(val), "kW"
        return float(val), "W"
    base, _ = split_standard(rec.get("model"))
    if kind == "modules":
        w = extract_watts(base)
        return (float(w), "W") if w else (None, None)
    if kind == "inverters":
        kw = extract_kw(base)
        return (kw, "kW") if kw else (None, None)
    return None, None


def display_brand(manufacturer: str | None, preferred: set[str] | None = None) -> str:
    brands = brands_for_manufacturer(manufacturer)
    if preferred:
        hit = brands & preferred
        if hit:
            return sorted(hit)[0]
    if len(brands) == 1:
        return next(iter(brands))
    if brands:
        return sorted(brands, key=len)[0]
    return brand_short(manufacturer)


def listing_row(kind: str, rec: dict, pull: dict | None) -> tuple:
    base, std = split_standard(rec.get("model"))
    rating, unit = rating_for(kind, rec)
    return (
        rec["id"], kind, rec.get("manufacturer", ""), display_brand(rec.get("manufacturer")), rec.get("series", ""),
        rec["model"], base, model_key(rec["model"]), std, rating, unit,
        rec.get("approval_date"), rec.get("expiry_date"),
        rec.get("first_seen") or (pull or {}).get("fetched_at", "")[:10], rec.get("last_seen") or (pull or {}).get("fetched_at", "")[:10],
        rec.get("delisted_date"), json.dumps(rec.get("extra") or {}, ensure_ascii=False),
        rec.get("source_file") or (pull or {}).get("raw_file", ""), rec.get("source_pull") or (pull or {}).get("fetched_at", ""),
    )


def build(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = _db_location(db_path or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # each process builds its own file and swaps it in atomically, so a check running
    # at the same moment never sees a half-built index
    tmp = db_path.with_name(f"{db_path.stem}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8")) if (DATA / "meta.json").exists() else {}
    for kind in KINDS:
        pull = meta.get(kind) or {}
        if pull:
            conn.execute("INSERT INTO pulls VALUES (?,?,?,?,?,?,?,?,?)", (
                kind, pull.get("label"), pull.get("source_url"), pull.get("raw_file"), pull.get("sha256"),
                pull.get("fetched_at"), pull.get("records"), pull.get("current_records"), json.dumps(pull.get("field_mapping") or {})))
        p = DATA / f"{kind}.json"
        if p.exists():
            recs = json.loads(p.read_text(encoding="utf-8"))
            conn.executemany("INSERT INTO listings VALUES (" + ",".join("?" * 19) + ")", (listing_row(kind, r, pull) for r in recs))
    for row in read_jobs():
        conn.execute(f"INSERT INTO jobs ({','.join(JOB_COLUMNS)}) VALUES ({','.join('?' * len(JOB_COLUMNS))})",
                     [row.get(c, "") for c in JOB_COLUMNS])
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (_fingerprint(),))
    conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),))
    conn.commit()
    conn.close()
    os.replace(tmp, db_path)
    return sqlite3.connect(db_path)


def connect(db_path: Path | None = None, rebuild: bool = False) -> sqlite3.Connection:
    """Open the index, rebuilding it when any source file changed."""
    db_path = _db_location(db_path or DB_PATH)
    if db_path.exists() and not rebuild:
        conn = sqlite3.connect(db_path)
        try:
            fp = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if fp and fp[0] == _fingerprint():
                conn.row_factory = sqlite3.Row
                return conn
        except sqlite3.DatabaseError:
            pass
        conn.close()
    conn = build(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def pulls(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["kind"]: dict(r) for r in conn.execute("SELECT * FROM pulls")}


class NoRegisterData(RuntimeError):
    """The register for a category is missing or empty. A check must never answer from no data."""


def listings(conn: sqlite3.Connection, kind: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM listings WHERE kind=?", (kind,))]
    if not rows:
        raise NoRegisterData(f"no {kind} listings loaded; the register data is missing, so no answer can be given")
    return rows


def manufacturers(conn: sqlite3.Connection, kind: str | None = None) -> list[str]:
    q = "SELECT DISTINCT manufacturer FROM listings" + (" WHERE kind=?" if kind else "")
    return [r[0] for r in conn.execute(q, (kind,) if kind else ())]


# ---------------------------------------------------------------- jobs log (CSV is the record)

def read_jobs(path: Path | None = None) -> list[dict]:
    path = path or JOBS_CSV
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def append_job(row: dict, path: Path | None = None) -> None:
    path = path or JOBS_CSV
    try:
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=JOB_COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({c: row.get(c, "") for c in JOB_COLUMNS})
    except OSError as e:
        print(f"(check not logged: {e})", file=sys.stderr)
