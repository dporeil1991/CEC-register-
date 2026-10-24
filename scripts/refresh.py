#!/usr/bin/env python3
"""Download the Clean Energy Council (CEC) approved-product lists and normalise them.

The CEC publishes its approved inverters, PV modules and batteries lists, and the
Clean Energy Regulator (CER) hosts the downloadable CSV/XLSX copies. This script:

  1. fetches each CER document page, finds the CSV/XLSX links on it (with known
     direct URLs as a fallback),
  2. downloads the file and saves the raw copy under data/raw/,
  3. normalises every row into a common JSON shape under data/<kind>.json,
  4. writes data/meta.json with source URLs, fetch time, counts and headers.

Usage:
  python3 scripts/refresh.py                      # refresh everything from CER
  python3 scripts/refresh.py --only inverters     # one list
  python3 scripts/refresh.py --from-file inverters path/to/file.csv
                                                  # ingest a manually downloaded file

Only the standard library is required for CSV; openpyxl is used if an XLSX is
the only format available.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 cec-register-refresh/1.0")

SOURCES = {
    "inverters": {
        "label": "CEC approved inverters",
        "page": "https://cer.gov.au/document_page/cec-approved-inverters",
        "fallback": [
            "https://cer.gov.au/document/cec-approved-inverters-0",
            "https://cer.gov.au/document/cec-approved-inverters",
        ],
    },
    "modules": {
        "label": "CEC approved PV modules",
        "page": "https://cer.gov.au/document_page/cec-approved-pv-modules",
        "fallback": [
            "https://cer.gov.au/document/cec-approved-pv-modules-0",
            "https://cer.gov.au/document/cec-approved-pv-modules",
        ],
    },
    "batteries": {
        "label": "CEC approved solar batteries",
        "page": "https://cer.gov.au/document_page/cec-approved-solar-batteries",
        "fallback": [
            "https://cer.gov.au/document/cec-approved-solar-batteries-0",
            "https://cer.gov.au/document/cec-approved-solar-batteries",
        ],
    },
}

# Header keywords used to find the canonical fields in whatever columns the
# source has this month. First match wins, matching is case-insensitive on a
# squashed (alphanumeric only) header.
FIELD_KEYS = {
    "manufacturer": ["manufacturer", "licensee", "certificateholder", "brand", "company", "applicant"],
    "series": ["series", "family", "productseries"],
    "model": ["modelnumber", "modelno", "model", "productmodel"],
    "approval_date": ["approvaldate", "approveddate", "cecapproveddate", "dateapproved", "listingdate", "startdate"],
    "expiry_date": ["expirydate", "expiry", "expirationdate", "expires", "enddate"],
    "power": ["acpower", "acpowerkw", "ratedpower", "nominalrating", "approvednominalrating", "ratedoutput",
              "pmax", "wattage", "capacity", "ratedcapacity", "usablecapacity", "energy"],
}


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str, timeout: int = 60) -> tuple[bytes, str, str]:
    """Return (body, final_url, content_type)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl(), resp.headers.get("Content-Type", "")


def find_file_links(page_html: str, base_url: str) -> list[str]:
    """Pull every link that looks like a CSV/XLSX download off a CER document page."""
    links: list[tuple[int, str]] = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_html, re.I | re.S):
        href = html.unescape(m.group(1))
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = html.unescape(text).lower()
        blob = (href + " " + text).lower()
        if not ("/document/" in href or re.search(r"\.(csv|xlsx?)(\?|$)", href, re.I)):
            continue
        if not re.search(r"csv|xls", blob):
            continue
        score = 0 if "csv" in blob else 1  # prefer CSV over XLSX
        links.append((score, urllib.parse.urljoin(base_url, href)))
    seen, out = set(), []
    for _, url in sorted(links):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def rows_from_bytes(body: bytes, hint_url: str) -> tuple[list[dict], str]:
    """Parse CSV or XLSX bytes into a list of dict rows. Returns (rows, format)."""
    if body[:2] == b"PK":  # zip container -> xlsx
        return rows_from_xlsx(body), "xlsx"
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = body.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None or "<html" in text[:2000].lower():
        raise ValueError(f"{hint_url}: not a CSV/XLSX payload")
    return rows_from_csv_text(text), "csv"


def rows_from_csv_text(text: str) -> list[dict]:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = text.splitlines()
    # Some exports have title/preamble rows before the header: skip until a row
    # that mentions "model".
    start = 0
    for i, line in enumerate(lines[:30]):
        if "model" in line.lower():
            start = i
            break
    reader = csv.reader(lines[start:], dialect)
    header = None
    rows = []
    for rec in reader:
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
        header = None
        rows: list[dict] = []
        for rec in ws.iter_rows(values_only=True):
            vals = ["" if v is None else (v.strftime("%d/%m/%Y") if isinstance(v, (dt.date, dt.datetime)) else str(v).strip()) for v in rec]
            if header is None:
                if any("model" in v.lower() for v in vals):
                    header = vals
                continue
            if not any(vals):
                continue
            rows.append({header[i] if i < len(header) and header[i] else f"col{i}": v for i, v in enumerate(vals)})
        if len(rows) > len(best):
            best = rows
    return best


DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y",
                "%d %B %Y", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y")


def parse_date(s: str) -> str | None:
    """Return ISO yyyy-mm-dd or None. Source files use Australian d/m/Y."""
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
    """Map canonical field -> actual header name."""
    squashed = {h: squash(h) for h in headers}
    mapping: dict[str, str] = {}
    for field, keys in FIELD_KEYS.items():
        for key in keys:
            hit = next((h for h, sq in squashed.items() if sq == key), None) or \
                  next((h for h, sq in squashed.items() if key in sq and h not in mapping.values()), None)
            if hit and hit not in mapping.values():
                mapping[field] = hit
                break
    return mapping


def normalise(rows: list[dict], kind: str) -> tuple[list[dict], dict[str, str]]:
    if not rows:
        return [], {}
    headers = list(rows[0].keys())
    mapping = map_headers(headers)
    if "model" not in mapping:
        raise ValueError(f"{kind}: could not find a model-number column in headers {headers}")
    out = []
    for i, r in enumerate(rows):
        model = r.get(mapping["model"], "").strip()
        if not model:
            continue
        rec = {
            "id": f"{kind[:3]}-{i+1}",
            "manufacturer": r.get(mapping.get("manufacturer", ""), "").strip(),
            "series": r.get(mapping.get("series", ""), "").strip() if "series" in mapping else "",
            "model": model,
            "approval_date": parse_date(r.get(mapping.get("approval_date", ""), "")),
            "expiry_date": parse_date(r.get(mapping.get("expiry_date", ""), "")),
            "power": parse_number(r.get(mapping.get("power", ""), "")) if "power" in mapping else None,
            "power_label": mapping.get("power", ""),
        }
        rec["extra"] = {k: v for k, v in r.items() if k not in mapping.values() and v}
        out.append(rec)
    return out, mapping


def save(kind: str, label: str, source_url: str, fmt: str, body: bytes, rows: list[dict], mapping: dict, meta: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    raw_path = RAW / f"{kind}.{fmt}"
    raw_path.write_bytes(body)
    (DATA / f"{kind}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
    today = dt.date.today().isoformat()
    current = sum(1 for r in rows if not r["expiry_date"] or r["expiry_date"] >= today)
    meta[kind] = {
        "label": label,
        "source_url": source_url,
        "format": fmt,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "records": len(rows),
        "current_records": current,
        "field_mapping": mapping,
        "raw_file": str(raw_path.relative_to(ROOT)) if raw_path.is_relative_to(ROOT) else str(raw_path),
    }
    log(f"  {kind}: {len(rows)} records ({current} current) from {source_url}")


def load_meta() -> dict:
    p = DATA / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_meta(meta: dict) -> None:
    meta["_about"] = ("Clean Energy Council approved products lists, downloaded from the Clean Energy "
                      "Regulator document pages. Dates are ISO yyyy-mm-dd. 'current_records' counts "
                      "listings whose expiry is on/after the fetch date.")
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
            body, final, ctype = fetch(url)
            rows, fmt = rows_from_bytes(body, final)
            if len(rows) < 50:
                log(f"  {url}: only {len(rows)} rows, skipping")
                continue
            norm, mapping = normalise(rows, kind)
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=sorted(SOURCES), action="append", help="refresh only this list (repeatable)")
    ap.add_argument("--from-file", nargs=2, metavar=("KIND", "PATH"), action="append",
                    help="ingest a manually downloaded CSV/XLSX instead of downloading")
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


if __name__ == "__main__":
    sys.exit(main())
