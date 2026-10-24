#!/usr/bin/env python3
"""cec.py - instant lookup against the CEC approved products register (inverters, PV modules, batteries).

Examples:
  python3 cec.py check "Fronius Primo GEN24 5.0"       # search every list, show status
  python3 cec.py check "JKM440N-54HL4R-B" --as-at 2025-06-30
  python3 cec.py inverters sungrow SG5.0RS              # one list only
  python3 cec.py modules "Trina" --limit 20 --all       # include expired listings
  python3 cec.py stats                                  # record counts + data age
  python3 cec.py check "SigenStor" --json               # machine-readable

Status meanings (as at today, or --as-at DATE):
  LISTED      on the CEC approved list and the listing has not expired
  EXPIRED     on the list but the listing expiry has passed
  NOT FOUND   no close match in the current register (may be de-listed or a typo)

Exit code: 0 if the best match is LISTED, 2 if EXPIRED, 1 if nothing matched or data missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KINDS = ("inverters", "modules", "batteries")
KIND_LABEL = {"inverters": "Inverter", "modules": "PV module", "batteries": "Battery"}
STALE_DAYS = 14


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9.]+", (s or "").lower()) if t]


def load(kind: str) -> list[dict]:
    p = DATA / f"{kind}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def load_meta() -> dict:
    p = DATA / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def haystack(rec: dict) -> str:
    """Searchable text: manufacturer, series, model, plus any brand-like extra column."""
    parts = [rec.get("manufacturer"), rec.get("series"), rec.get("model")]
    parts += [v for k, v in (rec.get("extra") or {}).items() if "brand" in k.lower()]
    return " ".join(x for x in parts if x)


def strip_suffix(model: str) -> str:
    """Drop bracketed standard tags such as '(AS4777-2 2020)' or '(AS/NZS 4777.2:2020)'."""
    return re.sub(r"\s*\((?:AS|AS/NZS|IEC)[^)]*\)\s*$", "", model or "", flags=re.I).strip()


def score(query: str, rec: dict) -> float:
    """0..1 relevance. Exact model match is 1.0; token coverage and fuzzy ratio otherwise."""
    q_sq = squash(query)
    m_sq = squash(rec.get("model", ""))
    if not q_sq:
        return 0.0
    if q_sq == m_sq or q_sq == squash(strip_suffix(rec.get("model", ""))):
        return 1.0
    if q_sq == squash(f"{rec.get('manufacturer','')} {strip_suffix(rec.get('model',''))}") or \
       q_sq == squash(f"{rec.get('series','')} {strip_suffix(rec.get('model',''))}"):
        return 0.99
    hay = haystack(rec)
    h_sq = squash(hay)
    best = 0.0
    if q_sq in m_sq:
        best = max(best, 0.9 + 0.09 * len(q_sq) / max(len(m_sq), 1))
    elif q_sq in h_sq:
        best = max(best, 0.8)
    # token coverage: every query token must appear somewhere in the haystack
    qt = tokens(query)
    if qt:
        hits = sum(1 for t in qt if squash(t) and squash(t) in h_sq)
        cov = hits / len(qt)
        if cov == 1.0:
            best = max(best, 0.75 + 0.1 * min(len(q_sq) / max(len(m_sq), 1), 1.0))
        elif cov >= 0.5:
            best = max(best, 0.45 * cov)
    # fuzzy on the model number itself for typos
    if len(q_sq) >= 4:
        r = difflib.SequenceMatcher(None, q_sq, m_sq).ratio()
        if r >= 0.75:
            best = max(best, 0.5 + 0.3 * (r - 0.75) / 0.25)
    return best


def status_of(rec: dict, as_at: dt.date) -> str:
    exp = rec.get("expiry_date")
    appr = rec.get("approval_date")
    if appr and dt.date.fromisoformat(appr) > as_at:
        return "NOT YET LISTED"
    if exp and dt.date.fromisoformat(exp) < as_at:
        return "EXPIRED"
    return "LISTED"


def search(query: str, kinds: tuple[str, ...], as_at: dt.date, include_expired: bool, limit: int, min_score: float = 0.4) -> list[dict]:
    results = []
    for kind in kinds:
        for rec in load(kind):
            s = score(query, rec)
            if s < min_score:
                continue
            st = status_of(rec, as_at)
            if st != "LISTED" and not include_expired and s < 0.9:
                continue
            results.append({**rec, "kind": kind, "score": round(s, 3), "status": st})
    results.sort(key=lambda r: (-r["score"], r["status"] != "LISTED", r.get("expiry_date") or "", r["model"]))
    return results[:limit]


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "-"
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def power_str(rec: dict) -> str:
    p = rec.get("power")
    if p is None:
        return ""
    label = squash(rec.get("power_label") or "")
    if "kwh" in label or (rec["kind"] == "batteries" and "kw" not in label and "w" not in label):
        unit = "kWh"
    elif "kw" in label:
        unit = "kW"
    elif "w" in label or rec["kind"] == "modules":
        unit = "W"
    else:
        unit = ""
    return f"{p:g} {unit}".strip()


def print_results(query: str, results: list[dict], as_at: dt.date, meta: dict) -> None:
    print(f'Query: "{query}"   as at {fmt_date(as_at.isoformat())}')
    print_freshness(meta)
    if not results:
        print("\nNOT FOUND - no listing matches. Check spelling/model suffix, or the product may be de-listed.")
        near = search(query, KINDS, as_at, True, 5, min_score=0.2)
        if near:
            print("  nearest listings:")
            for r in near:
                print(f"    {r['status']:<8} {KIND_LABEL[r['kind']]:<9} {r['manufacturer']} - {r['model']} (expires {fmt_date(r.get('expiry_date'))})")
        return
    print()
    w_kind = max(len(KIND_LABEL[r["kind"]]) for r in results)
    for r in results:
        badge = {"LISTED": "LISTED  ", "EXPIRED": "EXPIRED ", "NOT YET LISTED": "FUTURE  "}[r["status"]]
        head = f"{badge} {KIND_LABEL[r['kind']]:<{w_kind}}  {r['manufacturer']}"
        if r.get("series"):
            head += f" | {r['series']}"
        print(head)
        line = f"         model: {r['model']}"
        if power_str(r):
            line += f"   ({power_str(r)})"
        print(line)
        print(f"         approved {fmt_date(r.get('approval_date'))}   expires {fmt_date(r.get('expiry_date'))}   match {r['score']:.2f}")
        extras = {k: v for k, v in (r.get("extra") or {}).items() if v and len(str(v)) < 60}
        if extras:
            print("         " + "; ".join(f"{k}: {v}" for k, v in list(extras.items())[:6]))
    best = results[0]
    print()
    if best["status"] == "LISTED":
        print(f"=> {best['manufacturer']} {best['model']} is on the CEC approved {KIND_LABEL[best['kind']].lower()} list "
              f"(approved {fmt_date(best['approval_date'])}, expires {fmt_date(best['expiry_date'])}).")
    elif best["status"] == "EXPIRED":
        print(f"=> {best['manufacturer']} {best['model']} WAS listed but the listing expired {fmt_date(best['expiry_date'])}.")
    else:
        print(f"=> {best['manufacturer']} {best['model']} was not yet listed on {fmt_date(as_at.isoformat())} "
              f"(approved {fmt_date(best['approval_date'])}).")


def print_freshness(meta: dict) -> None:
    for kind in KINDS:
        m = meta.get(kind)
        if not m:
            print(f"  ! no data for {kind} - run scripts/refresh.py")
            continue
        fetched = dt.datetime.fromisoformat(m["fetched_at"]).date()
        age = (dt.date.today() - fetched).days
        warn = "  <-- STALE, run scripts/refresh.py" if age > STALE_DAYS else ""
        print(f"  data: {kind:<9} {m['records']:>5} records, fetched {fmt_date(fetched.isoformat())} ({age}d old){warn}")


def cmd_stats(meta: dict) -> int:
    if not meta:
        print("No data yet. Run: python3 scripts/refresh.py")
        return 1
    today = dt.date.today()
    for kind in KINDS:
        m = meta.get(kind)
        if not m:
            print(f"{kind}: no data")
            continue
        recs = load(kind)
        current = sum(1 for r in recs if status_of(r, today) == "LISTED")
        mans = len({(r.get("manufacturer") or "").lower() for r in recs})
        print(f"{m['label']}: {len(recs)} listings, {current} current, {mans} manufacturers")
        print(f"  source: {m['source_url']}")
        print(f"  fetched: {m['fetched_at']}  format: {m['format']}")
        print(f"  columns: {', '.join(f'{k}<-{v}' for k, v in m['field_mapping'].items())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def add_search(name: str, kinds: tuple[str, ...], help_: str):
        p = sub.add_parser(name, help=help_)
        p.add_argument("query", nargs="+", help="manufacturer / model text")
        p.add_argument("--as-at", help="evaluate status as at this date (yyyy-mm-dd or dd/mm/yyyy), e.g. install date")
        p.add_argument("--all", action="store_true", help="include expired listings in the results")
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--json", action="store_true", help="JSON output")
        p.set_defaults(kinds=kinds)

    add_search("check", KINDS, "search inverters, modules and batteries")
    add_search("inverters", ("inverters",), "search inverters / PCE only")
    add_search("modules", ("modules",), "search PV modules only")
    add_search("batteries", ("batteries",), "search batteries only")
    sub.add_parser("stats", help="record counts, sources and data age")

    args = ap.parse_args(argv)
    meta = load_meta()
    if args.cmd == "stats":
        return cmd_stats(meta)
    if not args.cmd:
        ap.print_help()
        return 1

    as_at = dt.date.today()
    if args.as_at:
        s = args.as_at.strip()
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        as_at = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else dt.date.fromisoformat(s)

    query = " ".join(args.query)
    if not any((DATA / f"{k}.json").exists() for k in args.kinds):
        print("No register data found. Run: python3 scripts/refresh.py", file=sys.stderr)
        return 1
    results = search(query, args.kinds, as_at, args.all, args.limit)
    if args.json:
        print(json.dumps({"query": query, "as_at": as_at.isoformat(), "results": results,
                          "data": {k: meta.get(k, {}).get("fetched_at") for k in args.kinds}}, indent=2, ensure_ascii=False))
    else:
        print_results(query, results, as_at, meta)
    if not results:
        return 1
    return 0 if results[0]["status"] == "LISTED" else 2


if __name__ == "__main__":
    sys.exit(main())
