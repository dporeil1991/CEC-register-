"""Command line interface: check, batch, expiring, jobs, stats, refresh."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

from . import match, report, store
from .store import KIND_LABEL, KINDS

STALE_DAYS = 30


def parse_day(s: str | None, default: dt.date) -> dt.date:
    if not s:
        return default
    s = s.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return dt.date.fromisoformat(s)


def pull_summary(conn, kinds, today: dt.date) -> tuple[list[str], str]:
    """Lines describing data freshness, and the newest pull date (ISO) for logging."""
    lines, newest = [], ""
    for k in kinds:
        p = store.pulls(conn).get(k)
        if not p or not p.get("pulled_at"):
            lines.append(f"  ! no {k} data - run: cec refresh")
            continue
        pulled = p["pulled_at"][:10]
        newest = max(newest, pulled)
        age = (today - dt.date.fromisoformat(pulled)).days
        warn = f"   <-- STALE ({age} days old, over {STALE_DAYS}): run cec refresh" if age > STALE_DAYS else ""
        lines.append(f"  {KIND_LABEL[k]:<9} register pulled {report.short_date(pulled)} ({age}d ago), {p['records']} listings, source {Path(p['source_file'] or '').name}{warn}")
    return lines, newest


def rating_str(r: dict) -> str:
    if r.get("rating") is None:
        return ""
    return f"{r['rating']:g} {r.get('rating_unit') or ''}".strip()


def do_check(conn, query: str, kind: str | None, as_at: dt.date, today: dt.date, limit: int) -> dict:
    kinds = (kind,) if kind else KINDS
    found = match.search(conn, query, kinds, as_at, limit=limit)
    return report.classify(found, as_at, today, kind)


def print_check(res: dict, conn, today: dt.date) -> None:
    kind_word = store.KIND_NOUN.get(res["kind"] or "", "any category")
    print(f"{res['query']}   ·   {kind_word}   ·   status as at {report.short_date(res['as_at'])}")
    for line in pull_summary(conn, (res["kind"],) if res["kind"] else KINDS, today)[0]:
        print(line)
    print()
    tag = {"APPROVED": "APPROVED", "APPROVED_EXPIRING": "APPROVED - EXPIRING SOON", "EXPIRED": "LISTING EXPIRED",
           "DELISTED": "REMOVED FROM LIST", "NOT_LISTED": "NOT LISTED"}[res["result"]]
    conf = "" if res["confidence"] in ("confident", "none") else f"   ({res['confidence']} match - confirm the listed name)"
    print(f"RESULT: {tag}{conf}")
    if res["listed_name"]:
        mt = {"exact": "exact", "variant": "variant: suffix differs", "prefix": "query is a prefix of the listed model",
              "contains": "partial model match", "tokens": "all words found", "fuzzy": "fuzzy"}.get(res["match_type"], res["match_type"])
        print(f"  listed as:       {res['listed_name']}   [{mt}]")
        print(f"  manufacturer:    {res['manufacturer']}")
        if res.get("rating") is not None:
            flag = ""
            if res.get("rating_check") == "mismatch":
                rq = res.get("rating_query") or {}
                given = next((f"{v:g} {u}" for k, u, v in (("watts", "W", rq.get("watts")), ("kw", "kW", rq.get("kw")), ("kwh", "kWh", rq.get("kwh"))) if v is not None), "?")
                flag = f"   <-- MISMATCH: nameplate says {given}; check you have the right model"
            print(f"  rating:          {res['rating']:g} {res['rating_unit']}{flag}")
        print(f"  approved:        {report.short_date(res['approved_date'])}      approved until: {report.short_date(res['approved_until'])}")
        if res.get("delisted_date"):
            print(f"  removed:         {report.short_date(res['delisted_date'])} (missing from the register pull on that date)")
    print(f"  checked:         {report.short_date(res['checked_on'])}")
    print()
    print("Report sentence:")
    print("  " + res["sentence"])
    if res["result"] == "APPROVED_EXPIRING" and res["as_at"] == res["checked_on"]:
        print(f"  Note: the listing lapses {report.long_date(res['approved_until'])}; a report issued after that date must say the listing has expired.")
    near = res["near"]
    if near:
        print()
        if res["result"] == "NOT_LISTED" and all(r.get("match_type") in ("brand", "brand-rating") for r in near):
            print(f"{res['brand']} listings at that rating (identify the model from the label, then re-check):")
        else:
            print("Nearest listings:" if res["result"] == "NOT_LISTED" else "Other listings in the same family:")
        for r in near:
            print(f"  {r['status']:<8} {KIND_LABEL[r['kind']]:<9} {r['brand']:<14} {r['model_listed']:<42} {rating_str(r):>10}   until {report.short_date(r.get('approved_until'))}   similarity {r['score']:.2f}")
    elif res["result"] == "NOT_LISTED":
        bir = res.get("brand_in_register") or {}
        if res["brand"] and bir and not any(bir.values()):
            print(f"\n  No {res['brand']} products appear in the {kind_word} register at all.")
        else:
            print("\n  Nothing similar found. Check the model number from the label photo and try without a suffix.")


def log_check(res: dict, job: str, data_pulled: str, note: str = "") -> None:
    store.append_job({
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"), "job": job or "", "kind": res["kind"] or "",
        "query": res["query"], "result": res["result"], "listed_name": res["listed_name"] or "",
        "manufacturer": res["manufacturer"] or "", "approved_until": res["approved_until"] or "",
        "as_at": res["as_at"], "data_pulled": data_pulled, "note": note or "",
    })


def cmd_check(args, conn) -> int:
    today = dt.date.today()
    words = list(args.words)
    kind = None
    if words:
        k, is_kind = store.resolve_kind(words[0])
        if is_kind:
            kind = k
            words = words[1:]
    if not words:
        print("usage: cec check [inverter|panel|battery] <model text> [--watts 440 | --kw 5 | --kwh 13.5]", file=sys.stderr)
        return 1
    query = " ".join(words)
    if args.watts:
        query += f" {args.watts}W"
    if args.kw:
        query += f" {args.kw}kW"
    if args.kwh:
        query += f" {args.kwh}kWh"
    as_at = parse_day(args.as_at, today)
    res = do_check(conn, query, kind, as_at, today, args.limit)
    _, newest = pull_summary(conn, (res["kind"],) if res["kind"] else KINDS, today)
    if args.json:
        out = {k: v for k, v in res.items() if k != "near"}
        out["near"] = [{k: r[k] for k in ("kind", "brand", "manufacturer", "model_listed", "rating", "rating_unit",
                                           "approved_until", "status", "score", "match_type")} for r in res["near"]]
        out["data_pulled"] = newest
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print_check(res, conn, today)
    if not args.no_log:
        log_check(res, args.job, newest, args.note)
    return {"APPROVED": 0, "APPROVED_EXPIRING": 0, "EXPIRED": 2, "DELISTED": 2, "NOT_LISTED": 1}[res["result"]]


def cmd_batch(args, conn) -> int:
    today = dt.date.today()
    with open(args.file, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("empty input", file=sys.stderr)
        return 1
    cols = {c.lower().strip(): c for c in rows[0].keys()}
    def col(*names):
        return next((cols[n] for n in names if n in cols), None)
    c_job, c_kind, c_model = col("job", "jn", "job_number", "job no"), col("kind", "category", "type"), col("model", "query", "product")
    c_watts, c_kw, c_asat = col("watts", "w"), col("kw", "kw_rating"), col("as_at", "as at", "install_date", "date")
    if not c_model:
        print(f"need a 'model' column; got {list(rows[0].keys())}", file=sys.stderr)
        return 1
    _, newest = pull_summary(conn, KINDS, today)
    out_cols = ["job", "kind", "query", "result", "result_label", "listed_name", "manufacturer", "approved_until", "as_at", "checked_on", "confidence", "sentence"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=out_cols)
    w.writeheader()
    worst = 0
    for r in rows:
        q = (r.get(c_model) or "").strip()
        if not q:
            continue
        kind, _ = store.resolve_kind(r.get(c_kind)) if c_kind else (None, False)
        if c_watts and (r.get(c_watts) or "").strip():
            q += f" {r[c_watts].strip()}W"
        if c_kw and (r.get(c_kw) or "").strip():
            q += f" {r[c_kw].strip()}kW"
        as_at = parse_day((r.get(c_asat) or "").strip() or None, today) if c_asat else today
        res = do_check(conn, q, kind, as_at, today, 6)
        job = (r.get(c_job) or "").strip() if c_job else ""
        w.writerow({"job": job, "kind": res["kind"] or "", "query": res["query"], "result": res["result"],
                    "result_label": res["result_label"], "listed_name": res["listed_name"] or "",
                    "manufacturer": res["manufacturer"] or "", "approved_until": res["approved_until"] or "",
                    "as_at": res["as_at"], "checked_on": res["checked_on"], "confidence": res["confidence"], "sentence": res["sentence"]})
        if not args.no_log:
            log_check(res, job, newest, "batch")
        worst = max(worst, {"APPROVED": 0, "APPROVED_EXPIRING": 0, "EXPIRED": 2, "DELISTED": 2, "NOT_LISTED": 1}[res["result"]])
    if args.out:
        Path(args.out).write_text(buf.getvalue(), encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(buf.getvalue())
    return worst


def latest_job_rows(rows: list[dict]) -> list[dict]:
    """Most recent log row per (job, kind, listed_name-or-query)."""
    latest: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("job", ""), r.get("kind", ""), (r.get("listed_name") or r.get("query") or "").lower())
        if key not in latest or r.get("checked_at", "") >= latest[key].get("checked_at", ""):
            latest[key] = r
    return sorted(latest.values(), key=lambda r: (r.get("job", ""), r.get("kind", "")))


def cmd_expiring(args, conn) -> int:
    today = dt.date.today()
    horizon = today + dt.timedelta(days=args.days)
    rows = latest_job_rows(store.read_jobs())
    if not rows:
        print("No checks logged yet (data/jobs.csv is empty).")
        return 0
    hits = []
    for r in rows:
        if not r.get("listed_name"):
            continue
        # the same listed name can appear twice (re-certified); take the row that is still current, else the latest
        order = " ORDER BY (delisted_date IS NOT NULL), approved_until DESC LIMIT 1"
        cur = conn.execute("SELECT * FROM listings WHERE kind=? AND model_listed=? AND manufacturer=?" + order,
                           (r["kind"], r["listed_name"], r["manufacturer"])).fetchone()
        if cur is None:
            cur = conn.execute("SELECT * FROM listings WHERE kind=? AND model_listed=?" + order, (r["kind"], r["listed_name"])).fetchone()
        if cur is None:
            hits.append({**r, "now": "NOT IN REGISTER", "until": r.get("approved_until", "")})
            continue
        cur = dict(cur)
        st = match.status_of(cur, today)
        until = cur.get("approved_until") or ""
        soon = until and dt.date.fromisoformat(until) <= horizon
        if st != "LISTED" or soon:
            hits.append({**r, "now": st, "until": until, "listing": cur})
    if not hits:
        print(f"Nothing in the jobs log expires within {args.days} days ({len(rows)} logged products checked against the current register).")
        return 0
    hits.sort(key=lambda h: (h["until"] or "9999", h["job"]))
    print(f"{len(hits)} logged product(s) expired, removed, or expiring within {args.days} days (today {report.short_date(today.isoformat())}):\n")
    print(f"{'Job':<10} {'Type':<9} {'Listed as':<44} {'Approved until':<15} {'Status now':<16} Days")
    for h in hits:
        days = (dt.date.fromisoformat(h["until"]) - today).days if h["until"] else None
        print(f"{h['job'] or '-':<10} {KIND_LABEL.get(h['kind'], h['kind']):<9} {h['listed_name']:<44} {report.short_date(h['until']):<15} {h['now']:<16} {days if days is not None else '-'}")
    if args.sentences:
        print()
        for h in hits:
            l = h.get("listing")
            if not l:
                continue
            res = {"kind": h["kind"], "brand": l["brand"], "model_query": l["model_base"], "listed_name": l["model_listed"],
                   "approved_until": l["approved_until"], "delisted_date": l.get("delisted_date"), "checked_on": today.isoformat(),
                   "result": "APPROVED_EXPIRING" if h["now"] == "LISTED" else h["now"]}
            print(f"[{h['job'] or '-'}] " + report.sentence(res))
    return 0


def cmd_jobs(args, conn) -> int:
    rows = store.read_jobs()
    if args.job:
        rows = [r for r in rows if r.get("job", "").lower() == args.job.lower()]
    rows = rows[-args.limit:]
    if not rows:
        print("No logged checks.")
        return 0
    for r in rows:
        print(f"{r['checked_at'][:16]:<17} {r['job'] or '-':<10} {KIND_LABEL.get(r['kind'], r['kind'] or 'any'):<9} {r['query']:<36} {r['result']:<18} {r['listed_name'] or '':<40} until {report.short_date(r['approved_until'] or None)}")
    return 0


def cmd_stats(args, conn) -> int:
    today = dt.date.today()
    for line in pull_summary(conn, KINDS, today)[0]:
        print(line)
    print()
    for k in KINDS:
        rows = store.listings(conn, k)
        cur = sum(1 for r in rows if match.status_of(r, today) == "LISTED")
        exp = sum(1 for r in rows if match.status_of(r, today) == "EXPIRED")
        dl = sum(1 for r in rows if r.get("delisted_date"))
        print(f"{KIND_LABEL[k]:<9} {len(rows):>5} listings: {cur} current, {exp} expired, {dl} removed since first pull, {len({r['manufacturer'] for r in rows})} manufacturers")
    jobs = store.read_jobs()
    print(f"\nJobs log: {len(jobs)} checks across {len({j['job'] for j in jobs if j.get('job')})} job numbers (data/jobs.csv)")
    print(f"Index: {store.DB_PATH} (rebuilt automatically when data/ changes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cec", description="CEC approved products register lookup.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("check", help='check one product: cec check inverter "SG5K-D" --job 51814')
    p.add_argument("words", nargs="*", help="[inverter|panel|battery] then the nameplate text")
    p.add_argument("--watts", type=float, help="panel wattage from the nameplate, e.g. 440")
    p.add_argument("--kw", type=float, help="inverter AC rating, e.g. 5")
    p.add_argument("--kwh", type=float, help="battery capacity, e.g. 13.5")
    p.add_argument("--as-at", help="status as at this date (dd/mm/yyyy or yyyy-mm-dd), e.g. the install date")
    p.add_argument("--job", default="", help="job number to log this check against")
    p.add_argument("--note", default="")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-log", action="store_true", help="do not append to data/jobs.csv")

    p = sub.add_parser("batch", help="check a CSV of job/model rows: columns job, kind, model[, watts, kw, as_at]")
    p.add_argument("file")
    p.add_argument("--out", help="write results CSV here instead of stdout")
    p.add_argument("--no-log", action="store_true")

    p = sub.add_parser("expiring", help="logged products whose listing lapses soon")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--sentences", action="store_true", help="also print a report sentence per product")

    p = sub.add_parser("jobs", help="show the checks log")
    p.add_argument("--job")
    p.add_argument("--limit", type=int, default=50)

    sub.add_parser("stats", help="register size, pull dates, log size")
    sub.add_parser("rebuild", help="force a rebuild of the SQLite index")
    sub.add_parser("refresh", help="download the lists from the Clean Energy Regulator (see cec refresh -h)", add_help=False)

    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "refresh":
        from . import refresh
        return refresh.main(argv[1:])
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 1
    conn = store.connect(rebuild=(args.cmd == "rebuild"))
    if args.cmd == "rebuild":
        print(f"rebuilt {store.DB_PATH}")
        return 0
    return {"check": cmd_check, "batch": cmd_batch, "expiring": cmd_expiring, "jobs": cmd_jobs, "stats": cmd_stats}[args.cmd](args, conn)
