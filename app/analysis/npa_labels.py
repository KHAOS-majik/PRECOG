"""Derive honest area labels from the incident data itself.

NPAs are statistical polygons with no canonical neighborhood name,
so we build labels from what we can actually verify: the modal CMPD
patrol division for the area and the most common street name among
incidents inside it. Nothing is invented.
"""
import re
import time
from collections import Counter
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal

_CACHE = {"data": {}, "ts": 0}
_TTL = 3600

_STREET_SUFFIXES = {
    "ST", "AVE", "AV", "RD", "DR", "BLVD", "LN", "CT", "PL", "WAY",
    "PKWY", "HWY", "TER", "CIR", "TRL", "LOOP", "SQ", "ALY", "RUN",
    "RW", "ROW", "CRES", "XING", "XRD",
}


def _normalize_street(addr):
    if not addr:
        return None
    # Strip leading number
    s = re.sub(r"^\d+\s+", "", addr.strip())
    # Keep up to the first suffix (inclusive) if we see one
    tokens = s.split()
    if not tokens:
        return None
    for i, t in enumerate(tokens):
        if t.upper() in _STREET_SUFFIXES:
            return " ".join(tokens[: i + 1]).title()
    # Fallback: first 2-3 tokens
    return " ".join(tokens[:3]).title()


def get_npa_labels():
    now = time.time()
    if _CACHE["data"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]

    session = SessionLocal()
    try:
        rows = (
            session.query(
                Incident.npa,
                Incident.address,
                Incident.cmpd_patrol_division,
                Incident.zip_code,
            )
            .filter(Incident.npa.isnot(None))
            .all()
        )
        by_npa = {}
        for r in rows:
            if not r.npa:
                continue
            if r.npa not in by_npa:
                by_npa[r.npa] = {"streets": Counter(), "divs": Counter(), "zips": Counter()}
            s = _normalize_street(r.address)
            if s:
                by_npa[r.npa]["streets"][s] += 1
            if r.cmpd_patrol_division:
                by_npa[r.npa]["divs"][str(r.cmpd_patrol_division)] += 1
            if r.zip_code:
                by_npa[r.npa]["zips"][str(r.zip_code)] += 1

        labels = {}
        for npa, cs in by_npa.items():
            top_street = cs["streets"].most_common(1)
            top_div = cs["divs"].most_common(1)
            top_zip = cs["zips"].most_common(1)
            street = top_street[0][0] if top_street else None
            div = top_div[0][0] if top_div else None
            zc = top_zip[0][0] if top_zip else None
            labels[str(npa)] = {
                "street": street,
                "division": div,
                "zip": zc,
            }

        _CACHE["data"] = labels
        _CACHE["ts"] = now
        return labels
    finally:
        session.close()


def label_for(npa, labels=None):
    """Short human-readable label: 'S Tryon St · Div 03 · 28202'."""
    labels = labels or get_npa_labels()
    info = labels.get(str(npa), {})
    parts = []
    if info.get("street"):
        parts.append(info["street"])
    if info.get("division"):
        parts.append(f"Div {info['division']}")
    if info.get("zip"):
        parts.append(info["zip"])
    return " · ".join(parts) if parts else f"Area {npa}"
