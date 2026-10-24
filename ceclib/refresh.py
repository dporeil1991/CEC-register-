"""Download the CEC lists from the Clean Energy Regulator and merge them into data/.

Rows are never deleted on re-import. Each category JSON holds every listing ever
seen: rows present in the new file get last_seen updated; rows missing from it are
kept and stamped delisted_date. A delisted row that reappears is revived.

  python3 cec.py refresh                           # download all three lists
  python3 cec.py refresh --only inverters
  python3 cec.py refresh --from-file modules ~/Downloads/cec-approved-pv-modules.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from .normalise import squash
from .store import DATA, KINDS

RAW = DATA / "raw"
ROOT = DATA.parent

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 cec-register-refresh/2.0")

SOURCES = {
    "inverters": {"label": "CEC approved inverters",
                  "page": "https://cer.gov.au/document_page/cec-approved-inverters",
                  "fallback": ["https://cer.gov.au/document/cec-approved-inverters-0",
                               "https://cer.gov.au/document/cec-approved-inverters"]},
    "modules": {"label": "CEC approved PV modules",
                "page": "https://cer.gov.au/document_page/cec-approved-pv-modules",
                "fallback": ["https://cer.gov.au/document/cec-approved-pv-modules-0",
                             "https://cer.gov.au/document/cec-approved-pv-modules"]},
    "batteries": {"label": "CEC approved solar batteries",
                  "page": "https://cer.gov.au/document_page/cec-approved-solar-batteries",
                  "fallback": ["https://cer.gov.au/document/cec-approved-solar-batteries-0",
                               "https://cer.gov.au/document/cec-approved-solar-batteries"]},
}

FIELD_KEYS = {
    "manufacturer": ["manufacturer", "licensee", "certificateholder", "brand", "company", "applicant"],
    "series": ["series", "family", "productseries"],
    "model": ["modelnumber", "modelno", "model", "productmodel"],
    "approval_date": ["approvaldate", "approveddate", "cecapproveddate", "dateapproved", "listingdate", "startdate"],
    "expiry_date": ["expirydate", "expiry", "expirationdate", "expires", "enddate"],
    "power": ["acpower", "acpowerkw", "ratedpower", "nominalrating", "approvednominalrating", "ratedoutput",
              "pmax", "wattage", "nominalbatterycapacity", "capacity", "ratedcapacity", "energy"],
}
DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y",
                "%d %B %Y", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str, timeout: int = 60) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl(), resp.headers.get("Content-Type", "")


def find_file_links(page_html: str, base_url: str) -> list[str]:
    links: list[tuple[int, str]] = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_html, re.I | re.S):
        href = html.unescape(m.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).lower()
        blob = (href + " " + text).lower()
        if not ("/document/" in href or re.search(r"\.(csv|xlsx?)(\?|$)", href, re.I)):
            continue
        if not re.search(r"csv|xls", blob):
            continue
        links.append((0 if "csv" in blob else 1, urllib.parse.urljoin(base_url, href)))
    seen, out = set(), []
    for _, url in sorted(links):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def rows_from_bytes(body: bytes, hint: str) -> tuple[list[dict], str]:
    if body[:2] == b"PK":
        return rows_from_xlsx(body), "xlsx"
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = body.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None or "<html" in text[:2000].lower():
        raise ValueError(f"{hint}: not a CSV/XLSX payload")
    return rows_from_csv_text(text), "csv"


def rows_from_csv_text(text: str) -> list[dict]:
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:30]) if "model" in line.lower()), 0)
    header, rows = None, []
    for rec in csv.reader(lines[start:], dialect):
        if header is None:
            if any(c.strip() for c in rec):
                header = [c.strip() for c in rec]
            continue
        if not any(c.strip() for c in rec):
            continue
        rows.append({header[i] if i < len(header) else f"col{i}": (c or "").strip() for i, c in enumerate(rec)})
    return rows


def rows_from_xlsx(body: bytes) -> list[dict]:
    try:
        import openpyxl  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("XLSX source needs openpyxl: pip install openpyxl") from e
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    best: list[dict] = []
    for ws in wb.worksheets:
        header, rows = None, []
        for rec in ws.iter_rows(values_only=True):
            vals = ["" if v is None else (v.strftime("%d/%m/%Y") if isinstance(v, (dt.date, dt.datetime)) else str(v).strip()) for v in rec]
            if header is None:
                if any("model" in v.lower() for v in vals):
                    header = vals
                continue
            if any(vals):
                rows.append({header[i] if i < len(header) and header[i] else f"col{i}": v for i, v in enumerate(vals)})
        if len(rows) > len(best):
            best = rows
    return best


def parse_date(s: str) -> str | None:
    s = (s or "").strip()
    if not s or s.lower() in {"n/a", "na", "none", "-", "nil"}:
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def parse_number(s: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", (s or "").replace(",", ""))
    return float(m.group(0)) if m else None


def map_headers(headers: list[str]) -> dict[str, str]:
    squashed = {h: squash(h) for h in headers}
    mapping: dict[str, str] = {}
    for field, keys in FIELD_KEYS.items():
        for key in keys:
            hit = next((h for h, sq in squashed.items() if sq == key and h not in mapping.values()), None) or \
                  next((h for h, sq in squashed.items() if key in sq and h not in mapping.values()), None)
            if hit:
                mapping[field] = hit
                break
    return mapping


def normalise(rows: list[dict], kind: str) -> tuple[list[dict], dict[str, str]]:
    if not rows:
        return [], {}
    mapping = map_headers(list(rows[0].keys()))
    if "model" not in mapping:
        raise ValueError(f"{kind}: no model-number column in headers {list(rows[0].keys())}")
    out = []
    for r in rows:
        model = r.get(mapping["model"], "").strip()
        if not model:
            continue
        rec = {
            "manufacturer": r.get(mapping.get("manufacturer", ""), "").strip(),
            "series": r.get(mapping["series"], "").strip() if "series" in mapping else "",
            "model": model,
            "approval_date": parse_date(r.get(mapping.get("approval_date", ""), "")),
            "expiry_date": parse_date(r.get(mapping.get("expiry_date", ""), "")),
            "power": parse_number(r.get(mapping["power"], "")) if "power" in mapping else None,
            "power_label": mapping.get("power", ""),
            "extra": {k: v for k, v in r.items() if k not in mapping.values() and v},
        }
        out.append(rec)
    return out, mapping


def identity(rec: dict) -> str:
    return squash(rec.get("manufacturer")) + "|" + squash(rec.get("model"))


def merge(previous: list[dict], fresh: list[dict], kind: str, pull_date: str, source_file: str, pull_stamp: str) -> tuple[list[dict], dict]:
    """Merge a fresh pull into the historical list. Returns (records, change summary)."""
    by_id = {identity(r): r for r in previous}
    seen: set[str] = set()
    added = revived = changed = 0
    n = max((int(r["id"].split("-")[1]) for r in previous if r.get("id", "").startswith(kind[:3] + "-")), default=0)
    merged: list[dict] = []
    for rec in fresh:
        key = identity(rec)
        if key in seen:  # duplicate row in the source file
            continue
        seen.add(key)
        old = by_id.get(key)
        if old is None:
            n += 1
            rec.update(id=f"{kind[:3]}-{n}", first_seen=pull_date, last_seen=pull_date, delisted_date=None,
                       source_file=source_file, source_pull=pull_stamp)
            added += 1
            merged.append(rec)
            continue
        if old.get("delisted_date"):
            revived += 1
        if (old.get("approval_date"), old.get("expiry_date")) != (rec.get("approval_date"), rec.get("expiry_date")):
            changed += 1
        old.update(series=rec.get("series", ""), model=rec["model"], manufacturer=rec["manufacturer"],
                   approval_date=rec["approval_date"], expiry_date=rec["expiry_date"], power=rec["power"],
                   power_label=rec["power_label"], extra=rec["extra"], last_seen=pull_date, delisted_date=None,
                   source_file=source_file, source_pull=pull_stamp)
        old.setdefault("first_seen", pull_date)
        merged.append(old)
    delisted = 0
    for key, old in by_id.items():
        if key in seen:
            continue
        if not old.get("delisted_date"):
            old["delisted_date"] = pull_date
            delisted += 1
        merged.append(old)
    merged.sort(key=lambda r: (bool(r.get("delisted_date")), squash(r["manufacturer"]), squash(r["model"])))
    return merged, {"added": added, "delisted_now": delisted, "revived": revived, "dates_changed": changed, "in_file": len(seen)}


def load_kind(kind: str) -> list[dict]:
    p = DATA / f"{kind}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save(kind: str, label: str, source_url: str, fmt: str, body: bytes, rows: list[dict], mapping: dict, meta: dict) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    pull_date = now.date().isoformat()
    pull_stamp = now.isoformat(timespec="seconds")
    raw_path = RAW / f"{kind}.{fmt}"
    raw_path.write_bytes(body)
    source_file = str(raw_path.relative_to(ROOT)) if raw_path.is_relative_to(ROOT) else str(raw_path)
    merged, summary = merge(load_kind(kind), rows, kind, pull_date, source_file, pull_stamp)
    (DATA / f"{kind}.json").write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in merged) + "\n]\n", encoding="utf-8")
    current = sum(1 for r in merged if not r.get("delisted_date") and (not r["expiry_date"] or r["expiry_date"] >= pull_date))
    meta[kind] = {
        "label": label, "source_url": source_url, "format": fmt, "fetched_at": pull_stamp,
        "sha256": hashlib.sha256(body).hexdigest(), "records": len(merged), "in_source_file": summary["in_file"],
        "current_records": current, "delisted_records": sum(1 for r in merged if r.get("delisted_date")),
        "field_mapping": mapping, "raw_file": source_file, "last_change": summary,
    }
    log(f"  {kind}: {summary['in_file']} rows in file; +{summary['added']} new, {summary['delisted_now']} delisted, "
        f"{summary['revived']} revived, {summary['dates_changed']} date changes; {current} current of {len(merged)} kept")
    return summary


def load_meta() -> dict:
    p = DATA / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_meta(meta: dict) -> None:
    meta["_about"] = ("Clean Energy Council approved products lists, downloaded from the Clean Energy Regulator "
                      "document pages. Dates are ISO yyyy-mm-dd. Rows missing from a new pull are kept with a "
                      "delisted_date rather than deleted.")
    (DATA / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def refresh_kind(kind: str, meta: dict) -> bool:
    src = SOURCES[kind]
    log(f"[{kind}] {src['label']}")
    candidates: list[str] = []
    try:
        page, final, _ = fetch(src["page"])
        candidates = find_file_links(page.decode("utf-8", "replace"), final)
        log(f"  found {len(candidates)} file link(s) on {final}")
    except Exception as e:  # noqa: BLE001
        log(f"  page fetch failed ({e}); using fallback URLs")
    for u in src["fallback"]:
        if u not in candidates:
            candidates.append(u)
    for url in candidates:
        try:
            body, final, _ = fetch(url)
            rows, fmt = rows_from_bytes(body, final)
            if len(rows) < 50:
                log(f"  {url}: only {len(rows)} rows, skipping")
                continue
            norm, mapping = normalise(rows, kind)
            previous = len(load_kind(kind))
            if previous and len(norm) < 0.5 * previous:
                log(f"  {url}: {len(norm)} rows is under half the previous {previous}; refusing to mass-delist")
                continue
            save(kind, src["label"], final, fmt, body, norm, mapping, meta)
            return True
        except Exception as e:  # noqa: BLE001
            log(f"  {url}: {e}")
    log(f"  FAILED to refresh {kind}")
    return False


def ingest_file(kind: str, path: Path, meta: dict) -> None:
    body = path.read_bytes()
    rows, fmt = rows_from_bytes(body, str(path))
    norm, mapping = normalise(rows, kind)
    save(kind, SOURCES[kind]["label"], f"file://{path.resolve()}", fmt, body, norm, mapping, meta)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cec refresh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=sorted(SOURCES), action="append")
    ap.add_argument("--from-file", nargs=2, metavar=("KIND", "PATH"), action="append")
    args = ap.parse_args(argv)
    DATA.mkdir(exist_ok=True)
    meta = load_meta()
    ok = True
    if args.from_file:
        for kind, path in args.from_file:
            if kind not in SOURCES:
                ap.error(f"unknown kind {kind}; choose from {sorted(SOURCES)}")
            ingest_file(kind, Path(path), meta)
    else:
        for kind in (args.only or sorted(SOURCES)):
            ok &= refresh_kind(kind, meta)
    write_meta(meta)
    return 0 if ok else 1
