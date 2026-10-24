"""Turn a ranked search into a reportable result and a formal sentence."""
from __future__ import annotations

import datetime as dt

from .match import EXPIRING_DAYS
from .store import KIND_NOUN

CONFIDENT = 0.95
PROBABLE = 0.75

RESULTS = {
    "APPROVED": "Approved",
    "APPROVED_EXPIRING": "Approved, listing expires within 6 months",
    "EXPIRED": "Listing expired",
    "DELISTED": "Removed from the list",
    "NOT_LISTED": "Not listed",
}


def long_date(iso: str | None) -> str:
    if not iso:
        return "date not stated"
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.day} {d:%B %Y}"


def short_date(iso: str | None) -> str:
    if not iso:
        return "-"
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


def classify(search: dict, as_at: dt.date, today: dt.date, kind_hint: str | None) -> dict:
    """Pick the best listing and decide the reportable result."""
    results = search["results"]
    parsed = search["parsed"]
    best = results[0] if results else None
    confidence = "none"
    if best:
        confidence = "confident" if best["score"] >= CONFIDENT else "probable" if best["score"] >= PROBABLE else "weak"
    if not best or confidence == "weak":
        result = "NOT_LISTED"
        chosen = None
    else:
        chosen = best
        st = best["status"]
        if st == "LISTED":
            until = best.get("approved_until")
            exp_soon = bool(until) and dt.date.fromisoformat(until) <= as_at + dt.timedelta(days=EXPIRING_DAYS)
            result = "APPROVED_EXPIRING" if exp_soon else "APPROVED"
        elif st == "EXPIRED":
            result = "EXPIRED"
        elif st == "DELISTED":
            result = "DELISTED"
        else:  # FUTURE: approved after the as-at date
            result = "NOT_LISTED"
    kind = (chosen or {}).get("kind") or kind_hint
    from .match import status_of
    status_today = status_of(chosen, today) if chosen else None
    brand = parsed["brand_text"] or ((chosen or {}).get("brand") or "")
    model_text = parsed["model"]
    if not model_text:
        rt = parsed["ratings"]
        model_text = (f"{rt['watts']:g} W" if "watts" in rt else f"{rt['kw']:g} kW" if "kw" in rt else f"{rt['kwh']:g} kWh" if "kwh" in rt else "")
        if not brand and not model_text:
            model_text = parsed["raw"]
    out = {
        "query": parsed["raw"],
        "brand": brand,
        "model_query": model_text,
        "kind": kind,
        "result": result,
        "result_label": RESULTS[result],
        "confidence": confidence,
        "listed_name": (chosen or {}).get("model_listed"),
        "manufacturer": (chosen or {}).get("manufacturer"),
        "standard": (chosen or {}).get("standard"),
        "approved_date": (chosen or {}).get("approved_date"),
        "approved_until": (chosen or {}).get("approved_until"),
        "delisted_date": (chosen or {}).get("delisted_date"),
        "rating": (chosen or {}).get("rating"),
        "rating_unit": (chosen or {}).get("rating_unit"),
        "match_type": (chosen or {}).get("match_type"),
        "score": (chosen or {}).get("score"),
        "as_at": as_at.isoformat(),
        "checked_on": today.isoformat(),
        "near": [r for r in results if r is not chosen][:6],
        "total_candidates": search.get("total", len(results)),
        "status_today": status_today,
        "rating_check": (chosen or {}).get("rating_check"),
        "rating_query": parsed["ratings"],
        "brand_in_register": search.get("brand_in_register"),
    }
    out["sentence"] = sentence(out)
    out["form_answer"] = form_answer(out)
    return out


def form_answer(res: dict) -> str:
    """Answer for the report form's "Is the <product> on the current CEC approved list" row.

    The form asks about the *current* list, so this always reflects status today,
    even when the check was run --as-at an install date.
    """
    listed = res.get("listed_name")
    # The form asks about the current list. A past --as-at (install date) is restated as of
    # today; a future --as-at is the planned issue date, so the result for that date stands.
    past = res.get("as_at", "") < res.get("checked_on", "")
    now = res.get("status_today") if past else None
    result = res["result"]
    if now:  # historical check: restate in terms of today
        result = {"LISTED": "APPROVED", "EXPIRED": "EXPIRED", "DELISTED": "DELISTED"}.get(now, "NOT_LISTED")
    if result in ("APPROVED", "APPROVED_EXPIRING"):
        return f"Yes – listed as {listed}, approved until {short_date(res['approved_until'])}."
    if result == "EXPIRED":
        return f"No – not on the current CEC approved list (the listing for {listed} expired on {short_date(res['approved_until'])})."
    if result == "DELISTED":
        return f"No – not on the current CEC approved list (the listing for {listed} was removed on or about {short_date(res.get('delisted_date'))})."
    noun = KIND_NOUN.get(res["kind"] or "", "product")
    if res.get("near") and all(r.get("match_type") in ("brand", "brand-rating") for r in res["near"]):
        return "Unable to confirm – the full model number is needed to match a specific CEC listing."
    bir = res.get("brand_in_register") or {}
    if res.get("brand") and bir and not any(bir.values()):
        return f"No – no {res['brand']} {noun}s are on the current CEC approved list."
    rating = _rating_text(res)
    if rating and res.get("brand") and not res.get("near") and _no_model(res):
        return f"No – no {res['brand']} {noun}s rated {rating} are on the CEC approved list, current or expired."
    return "No – not on the current CEC approved list."


def _rating_text(res: dict) -> str:
    rq = res.get("rating_query") or {}
    if "watts" in rq:
        return f"{rq['watts']:g} W"
    if "kw" in rq:
        return f"{rq['kw']:g} kW"
    if "kwh" in rq:
        return f"{rq['kwh']:g} kWh"
    return ""


def _no_model(res: dict) -> bool:
    """True when the query held only a brand and a rating, with no model number."""
    return res.get("model_query", "").strip().lower() == _rating_text(res).lower()


def sentence(res: dict) -> str:
    noun = KIND_NOUN.get(res["kind"] or "", "product")
    name = " ".join(x for x in (res["brand"], res["model_query"]) if x).strip()
    checked = long_date(res["checked_on"])
    listed = res.get("listed_name")
    historical = res.get("as_at") and res["as_at"] != res["checked_on"]
    if res["result"] in ("APPROVED", "APPROVED_EXPIRING"):
        as_listed = f' as "{listed}"' if listed and listed.strip().lower() != res["model_query"].strip().lower() else ""
        if historical:
            now = res.get("status_today")
            tail = {"EXPIRED": f"the listing has since expired on {long_date(res['approved_until'])}",
                    "DELISTED": f"the listing has since been removed from the list on or about {long_date(res.get('delisted_date'))}",
                    }.get(now, f"the listing remains current, with approval valid until {long_date(res['approved_until'])}")
            return (f"As at {long_date(res['as_at'])}, the {name} {noun} was listed on the Clean Energy Council (CEC) approved "
                    f"products list{as_listed}; {tail} (checked {checked}).")
        return (f"The {name} {noun} is currently listed on the Clean Energy Council (CEC) approved products list{as_listed}, "
                f"with approval valid until {long_date(res['approved_until'])} (checked {checked}).")
    if res["result"] == "EXPIRED":
        return (f'The {name} {noun} is not currently listed on the CEC approved products list '
                f'(the listing for "{listed}" expired on {long_date(res["approved_until"])}; checked {checked}).')
    if res["result"] == "DELISTED":
        return (f'The {name} {noun} is not currently listed on the CEC approved products list '
                f'(the listing for "{listed}" was removed from the list on or about {long_date(res["delisted_date"])}; checked {checked}).')
    bir = res.get("brand_in_register") or {}
    if res["brand"] and bir and not any(bir.values()):
        return (f"The {name} {noun} is not listed on the CEC approved products list, and no {res['brand']} "
                f"{noun}s appear on the list (checked {checked}).")
    rating = _rating_text(res)
    if rating and res["brand"] and not res.get("near") and not res.get("listed_name") and _no_model(res):
        return (f"The {name} {noun} is not listed on the CEC approved products list; no current or expired "
                f"{res['brand']} {noun} listing rated {rating} was found (checked {checked}).")
    if res.get("near") and res.get("match_type") is None and all(r.get("match_type") in ("brand", "brand-rating") for r in res["near"]):
        n = res.get("total_candidates") or len(res["near"])
        return (f"The {name} {noun} could not be matched to a specific CEC listing without the full model number; "
                f"{n} {res['brand']} {noun} listings at this rating were found (checked {checked}).")
    return (f"The {name} {noun} is not listed on the CEC approved products list, and no current or expired "
            f"listing for this model was found (checked {checked}).")
