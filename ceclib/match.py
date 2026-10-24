"""Ranking listings against a free-text nameplate query."""
from __future__ import annotations

import datetime as dt
import difflib

from .normalise import brands_for_manufacturer, model_key, split_query, split_standard, squash, tokens
from .store import KINDS, listings, manufacturers

EXPIRING_DAYS = 183  # "approved but expiring" window: six months


def status_of(rec: dict, as_at: dt.date) -> str:
    """LISTED / EXPIRED / DELISTED / FUTURE for a listing as at a date."""
    appr, until, delisted = rec.get("approved_date"), rec.get("approved_until"), rec.get("delisted_date")
    if appr and appr > as_at.isoformat():
        return "FUTURE"
    if delisted and delisted <= as_at.isoformat():
        return "DELISTED"
    if until and until < as_at.isoformat():
        return "EXPIRED"
    return "LISTED"


def _rating_match(q: dict, rec: dict) -> str:
    """'match', 'mismatch' or 'unknown' between a query rating and the listing rating."""
    r, unit = rec.get("rating"), rec.get("rating_unit")
    want = None
    if unit == "W" and "watts" in q:
        want = float(q["watts"])
    elif unit == "kW" and "kw" in q:
        want = float(q["kw"])
    elif unit == "kWh" and "kwh" in q:
        want = float(q["kwh"])
    elif unit == "W" and "kw" in q:
        want = float(q["kw"]) * 1000
    if want is None or r is None:
        return "unknown"
    tol = 0.011 * max(abs(want), 1)
    if unit == "kW":
        tol = max(tol, 0.06)  # 4.999 vs 5, 4.6 vs 5 is a different variant though
    return "match" if abs(r - want) <= tol else "mismatch"


def score(parsed: dict, rec: dict) -> tuple[float, str]:
    """Return (score 0..1, match_type)."""
    if not parsed["model"]:
        return score_without_model(parsed, rec)
    q_model = parsed["model"]
    q_base, _ = split_standard(q_model)
    q_base_sq = squash(q_base)
    q_key = model_key(q_model)
    r_base_sq = squash(rec["model_base"])
    r_key = rec["model_key"] or ""
    best, kind = 0.0, "none"
    if q_base_sq and q_base_sq == r_base_sq:
        best, kind = 1.0, "exact"
    elif q_key and q_key == r_key:
        best, kind = 0.97, "variant"
    elif len(q_key) >= 4 and r_key.startswith(q_key):
        best, kind = 0.92, "prefix"
    elif len(q_key) >= 4 and q_key in r_key:
        best, kind = 0.88, "contains"
    elif len(r_key) >= 4 and r_key in q_key:
        best, kind = 0.85, "contains"
    hay = squash(" ".join([rec["manufacturer"], rec.get("series") or "", rec["model_base"], rec.get("brand") or ""]))
    if best < 0.75:
        toks = [squash(t) for t in tokens(q_model)]
        toks = [t for t in toks if t]
        if toks:
            hits = sum(1 for t in toks if t in hay)
            cov = hits / len(toks)
            if cov == 1.0:
                best, kind = max(best, 0.75), (kind if best >= 0.75 else "tokens")
            elif cov >= 0.5:
                if 0.45 * cov > best:
                    best, kind = 0.45 * cov, "partial"
    if best < 0.75 and len(q_key) >= 4 and r_key:
        r = difflib.SequenceMatcher(None, q_key, r_key).ratio()
        if r >= 0.8:
            fz = 0.5 + 0.25 * (r - 0.8) / 0.2
            if fz > best:
                best, kind = fz, "fuzzy"
    if best == 0:
        return 0.0, "none"
    # manufacturer agreement
    if parsed["brands"]:
        rec_brands = brands_for_manufacturer(rec["manufacturer"])
        man_sq = squash(rec["manufacturer"])
        agree = bool(rec_brands & parsed["brands"]) or any(squash(b) in man_sq for b in parsed["brands"])
        best += 0.02 if agree else -0.3
    # rating agreement
    rm = _rating_match(parsed["ratings"], rec)
    if rm == "match":
        best += 0.03
    elif rm == "mismatch":
        best -= 0.2
    return max(0.0, min(best, 1.0)), kind


def score_without_model(parsed: dict, rec: dict) -> tuple[float, str]:
    """Brand and/or rating only ("Trina 440W"): list that brand's products at that rating, never as a definite match."""
    if not parsed["brands"] and not parsed["ratings"]:
        return 0.0, "none"
    if parsed["brands"]:
        rec_brands = brands_for_manufacturer(rec["manufacturer"])
        man_sq = squash(rec["manufacturer"])
        if not (rec_brands & parsed["brands"] or any(squash(b) in man_sq for b in parsed["brands"])):
            return 0.0, "none"
    rm = _rating_match(parsed["ratings"], rec)
    if parsed["ratings"] and rm != "match":
        return 0.0, "none"
    return (0.6 if parsed["brands"] and parsed["ratings"] else 0.4), "brand-rating" if parsed["ratings"] else "brand"


def search(conn, query: str, kinds: tuple[str, ...] | None, as_at: dt.date, limit: int = 8, min_score: float = 0.3) -> dict:
    kinds = kinds or KINDS
    parsed = split_query(query, manufacturers(conn))
    results = []
    brand_seen_in_kind = {k: False for k in kinds}
    for kind in kinds:
        for rec in listings(conn, kind):
            if parsed["brands"]:
                rb = brands_for_manufacturer(rec["manufacturer"])
                if rb & parsed["brands"] or any(squash(b) in squash(rec["manufacturer"]) for b in parsed["brands"]):
                    brand_seen_in_kind[kind] = True
            s, mtype = score(parsed, rec)
            if s < min_score:
                continue
            rec = dict(rec)
            rec.update(score=round(s, 3), match_type=mtype, status=status_of(rec, as_at),
                       rating_check=_rating_match(parsed["ratings"], rec))
            results.append(rec)
    order = {"LISTED": 0, "FUTURE": 1, "EXPIRED": 2, "DELISTED": 3}
    results.sort(key=lambda r: (-r["score"], order[r["status"]], r.get("approved_until") or "", r["model_listed"]))
    return {"parsed": parsed, "results": results[:limit], "total": len(results),
            "brand_in_register": {k: v for k, v in brand_seen_in_kind.items()} if parsed["brands"] else None}
