"""Model-number and manufacturer normalisation.

The register lists products in whatever form the certificate holder wrote them:
"SG5K-D-NI (AS4777-2 2020)", "Primo GEN24 10.0 Plus", "RSM108-9-440NDG". A
nameplate photo rarely matches that exactly, so everything is reduced to a
comparable *key* before matching, while the listed text is always kept.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALIASES_PATH = ROOT / "data" / "aliases.json"

# Trailing standards bracket, e.g. "(AS4777-2 2020)", "(AS/NZS 4777.2:2020)", "(IEC 61215-2021)".
STANDARD_RE = re.compile(r"\s*\(\s*((?:AS/NZS|AS|IEC|EN|UL|IEEE)[\s\d/.:\-][^)]*)\)\s*$", re.I)

# Variant suffixes that do not change the certified product family. Matched as a
# trailing token (after a space or hyphen) or, for DG, glued onto the model.
KNOWN_SUFFIXES = ("NI", "ADA", "AUS", "SC", "PLUS", "DG")
_SUFFIX_SEP_RE = re.compile(r"[\s\-_/]+(?:" + "|".join(KNOWN_SUFFIXES) + r")$", re.I)
_SUFFIX_GLUED_RE = re.compile(r"(?<=\d[A-Z])DG$|(?<=\d)DG$", re.I)

# Legal/corporate noise removed when deriving a short brand from a manufacturer name.
_LEGAL_RE = re.compile(
    r"\b(co\.?|ltd\.?|limited|pty\.?|inc\.?|corp\.?|corporation|gmbh|spa|s\.?p\.?a\.?|srl|s\.?r\.?l\.?|"
    r"bv|b\.v\.|ag|as|a\s*s|sa|pte|llc|plc|company|holding|holdings|group|technolog(?:y|ies)|"
    r"technic|tech|power|supply|energy|new|solar|electric(?:al|s)?|industries|international|"
    r"australia|australian|\(aust\)|\(australia\)|\(qld\)|\(shanghai\)|\(zhejiang\)|\(zhe\s*jiang\)|"
    r"\(jiangsu\)|\(suzhou\)|\(guangde\)|photovoltaic|renewables?|development|digital|operations|"
    r"system|systems|integration|storage|magnetics|precision|science|automation|equipment)\b\.?",
    re.I,
)

STOPWORDS = {"inverter", "inverters", "panel", "panels", "module", "modules", "battery", "batteries",
             "solar", "hybrid", "pv", "the", "and", "kw", "w", "kwh", "model", "series", "string",
             "single", "phase", "three", "grid", "tied", "tie"}


def squash(s: str | None) -> str:
    """Lowercase alphanumerics only. Used for loose containment tests."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tokens(s: str | None) -> list[str]:
    return [t for t in re.split(r"[\s,;()\[\]]+", (s or "").strip()) if t]


def split_standard(model: str | None) -> tuple[str, str | None]:
    """'SG5K-D-NI (AS4777-2 2020)' -> ('SG5K-D-NI', 'AS4777-2 2020')."""
    model = (model or "").strip()
    m = STANDARD_RE.search(model)
    if not m:
        return model, None
    return model[: m.start()].strip(), re.sub(r"\s+", " ", m.group(1)).strip()


def strip_suffixes(base: str) -> str:
    """Remove known trailing variant suffixes, repeatedly: 'SG5K-D-NI' -> 'SG5K-D'."""
    s = base.strip()
    while True:
        new = _SUFFIX_SEP_RE.sub("", s)
        new = _SUFFIX_GLUED_RE.sub("", new)
        new = new.rstrip(" -_/")
        if new == s or not new:
            return s if not new else new
        s = new


def model_key(model: str | None) -> str:
    """Comparable key: no standards bracket, no known suffixes, no spaces/hyphens/underscores, upper-case.

    Dots and slashes are kept because they distinguish real products (SG5.0RS, JAM60S10-330/PR).
    """
    base, _ = split_standard(model)
    base = strip_suffixes(base)
    return re.sub(r"[\s\-_]+", "", base).upper()


def extract_watts(text: str | None) -> int | None:
    """Panel nameplate wattage hidden in a model string: 'RSM108-9-440NDG' -> 440."""
    if not text:
        return None
    m = re.search(r"(\d{3})\s*W(?:p|att)?\b", text, re.I)
    if m:
        return int(m.group(1))
    cands = [int(x) for x in re.findall(r"(?<![\d.])(\d{3})(?![\d])", text)]
    cands = [c for c in cands if 150 <= c <= 800]
    return cands[-1] if cands else None


def extract_kw(text: str | None) -> float | None:
    """Inverter AC rating hidden in a model string: 'SG5K-D' -> 5.0, 'Primo 5.0-1' -> 5.0, 'X1-MINI-1.5K-G4' -> 1.5."""
    if not text:
        return None
    m = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s*K(?:W|VA|TL|TLX|TLM|TLA)?(?![A-Z0-9]{3,})", text, re.I)
    if m:
        v = float(m.group(1))
        if 0.2 <= v <= 500:
            return v
    m = re.search(r"(?<![\d.])(\d{1,3}\.\d)(?![\d])", text)
    if m:
        v = float(m.group(1))
        if 0.2 <= v <= 500:
            return v
    m = re.search(r"(?<![\d.])(\d{4,6})(?![\d])", text)
    if m and 1000 <= int(m.group(1)) <= 500000:
        return int(m.group(1)) / 1000
    return None


def parse_rating_from_query(query: str) -> tuple[str, dict]:
    """Pull explicit ratings like '440W', '5kW', '13.5kWh' out of a query. Returns (query_without_them, ratings)."""
    ratings: dict = {}
    def _take(m):
        val, unit = m.group(1), m.group(2).lower()
        if unit == "kwh":
            ratings["kwh"] = float(val)
        elif unit == "kw":
            ratings["kw"] = float(val)
        else:
            ratings["watts"] = int(float(val))
        return " "
    q = re.sub(r"(?<![\w.])(\d{1,4}(?:\.\d{1,2})?)\s*(kWh|kW|W)(?![\w])", _take, query, flags=re.I)
    return re.sub(r"\s+", " ", q).strip(), ratings


def brand_short(manufacturer: str | None) -> str:
    """'Sungrow Power Supply Co Ltd' -> 'Sungrow'; 'Fronius Australia Pty Ltd' -> 'Fronius'."""
    name = (manufacturer or "").strip()
    cleaned = _LEGAL_RE.sub(" ", name)
    cleaned = re.sub(r"[(),.]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or name


# ---------------------------------------------------------------- aliases

@lru_cache(maxsize=1)
def load_aliases(path: str | None = None) -> dict[str, list[str]]:
    """brand -> list of squashed manufacturer-name patterns (brand itself included)."""
    p = Path(path) if path else ALIASES_PATH
    brands = {}
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        brands = raw.get("brands", raw)
    out: dict[str, list[str]] = {}
    for brand, pats in brands.items():
        if brand.startswith("_"):
            continue
        pats = pats if isinstance(pats, list) else [pats]
        sq = [squash(x) for x in pats if squash(x)]
        bsq = squash(brand)
        if bsq and bsq not in sq:
            sq.insert(0, bsq)
        out[brand] = sq
    return out


def brands_for_manufacturer(manufacturer: str | None) -> set[str]:
    """Brands whose alias patterns appear in this manufacturer string."""
    msq = squash(manufacturer)
    if not msq:
        return set()
    return {b for b, pats in load_aliases().items() if any(p in msq for p in pats)}


def split_query(query: str, known_manufacturers: list[str] | None = None) -> dict:
    """Separate a free-text query into brand words, model text and ratings.

    'Sungrow SG5K-D 5kW' -> {'brands': {'Sungrow'}, 'brand_text': 'Sungrow', 'model': 'SG5K-D', 'ratings': {'kw': 5.0}}
    """
    q, ratings = parse_rating_from_query(query)
    q, standard = split_standard(q)  # a pasted listed name may carry its "(AS4777-2 2020)" tag
    toks = tokens(q)
    tsq = [squash(t) for t in toks]
    used = [False] * len(toks)
    brands: set[str] = set()
    aliases = load_aliases()
    # try longest runs of consecutive tokens first so "JA Solar" beats "Solar"
    for n in range(min(3, len(toks)), 0, -1):
        for i in range(0, len(toks) - n + 1):
            if any(used[i:i + n]):
                continue
            run = "".join(tsq[i:i + n])
            if len(run) < 3:
                continue
            hit = False
            for brand, pats in aliases.items():
                if run in pats:
                    brands.add(brand)
                    hit = True
            if (not hit and known_manufacturers and n == 1 and i == 0 and len(run) >= 4
                    and run not in STOPWORDS and run.upper() not in KNOWN_SUFFIXES and not any(ch.isdigit() for ch in run)):
                # the leading word of a query that is also the leading word of a manufacturer name is a brand
                for man in known_manufacturers:
                    words = [squash(w) for w in tokens(man)]
                    if words and words[0] == run:
                        brands.add(brand_short(man) or man)
                        hit = True
                        break
            if hit:
                for k in range(i, i + n):
                    used[k] = True
    model_toks = [t for t, u, s in zip(toks, used, tsq) if not u and s not in STOPWORDS]
    brand_toks = [t for t, u in zip(toks, used) if u]
    return {"brands": brands, "brand_text": " ".join(brand_toks), "model": " ".join(model_toks), "ratings": ratings,
            "standard": standard, "raw": query}
