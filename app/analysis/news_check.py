"""Line local news stories up against CMPD incident reports.

Google News gives headlines only, so a match rests on three things a headline
usually carries: the kind of crime, the day, and a rough location (a street, a
neighbourhood, or "south Charlotte"). When those leave more than one police
report standing, the story is reported as undecided rather than guessed.
"""
import math
import re
import time
from datetime import datetime, timedelta

from app.clock import data_through
from app.db import haversine_km
from app.models.incident import Incident, SessionLocal
from app.models.news import NewsArticle

WINDOW_DAYS = 90
# A first report normally runs the day of the incident or the day after.
DAYS_BEFORE_PUBLICATION = 2
# CMPD publishes about two days behind, so a newer story cannot be judged yet.
PUBLICATION_LAG_DAYS = 3
UPTOWN = (35.2271, -80.8431)
UPTOWN_RADIUS_KM = 2.0
NEAR_PLACE_KM = 2.5

CATEGORIES = [
    # (label, NIBRS codes, headline pattern) — first match wins, most serious first.
    ("Homicide", {"09A", "09B"}, r"\b(homicide|murder(ed)?|kill(s|ed|ing)?|dead|deadly|fatal(ly)?|dies|died|death|body found)\b"),
    ("Shooting", {"13A", "09A"}, r"\b(shot|shooting|shoots|gunfire|gunshot)\b"),
    ("Stabbing", {"13A", "09A"}, r"\b(stabb(ed|ing)|stabs)\b"),
    ("Kidnapping", {"100"}, r"\b(kidnapp?(ed|ing)|abduct(ed|ion))\b"),
    ("Sexual assault", {"11A", "11B", "11C", "11D"}, r"\b(rape[ds]?|sexual(ly)? assault(ed)?)\b"),
    ("Robbery", {"120"}, r"\b(robb(ed|ery|eries|ing)|carjack(ed|ing)?|armed hold-?up)\b"),
    ("Burglary", {"220"}, r"\b(burglar(y|ies|ized)|break-?ins?|broke into)\b"),
    ("Assault", {"13A", "13B"}, r"\b(assault(ed|ing|s)?|attack(ed|s)?|beat(en|ing)|punch(ed|ing)?)\b"),
]
ALL_CODES = set().union(*(codes for _, codes, _ in CATEGORIES))

# Stories about an earlier case rather than a new incident.
FOLLOW_UP = re.compile(
    r"\b(arrest(s|ed)?|charged|charges|indict(s|ed|ment)|sentenc(ed|ing)|trial|guilty|convict(ed|ion)|plea(ds|d)?|"
    r"court|jury|judge|lawsuit|sues|vigil|funeral|remembrance|memorial|protest(s|ers)?|family of|identif(y|ies|ied)|"
    r"wanted|manhunt|reward|in custody|suspect (found|caught|captured)|years? (ago|later)|anniversary|cold case|"
    r"bond|bonds out|released from|video shows|footage|accused|turns? (himself|herself) in|ruled|justifi\w+|"
    r"updates?|details|outlines|records|audit|calls for|loses job|linked to|connected (with|to))\b", re.I)
NOT_AN_INCIDENT = re.compile(
    r"\b(crash|wreck|collision|hit-and-run|fire|crime (rate|stats|statistics|data|trends?)|governor|gov\.|mayor|"
    r"council|legislat\w+|bill|policy|trump|national guard|opinion|editorial|report card|ranking|safest)\b", re.I)
ELSEWHERE = re.compile(
    r"\b(Gastonia|Rock Hill|Concord|Kannapolis|Salisbury|Monroe|Mooresville|Statesville|Hickory|Greensboro|"
    r"Raleigh|Durham|Winston-Salem|Fayetteville|Asheville|Wilmington|Alamance|Burlington|Lancaster|Fort Mill|"
    r"York County|Union County|Gaston County|Cabarrus|Iredell|Rowan County|Lincoln County|Florida|Georgia|"
    r"Virginia|Tennessee|South Carolina|Charlotte County)\b")
CHARLOTTE = re.compile(r"\b(Charlotte|CMPD|Mecklenburg|Queen City)\b", re.I)

DIRECTIONS = {
    "north": 0, "northeast": 45, "east": 90, "southeast": 135,
    "south": 180, "southwest": 225, "west": 270, "northwest": 315,
}
DIRECTION = re.compile(r"\b(north|south)?(east|west)?\s+Charlotte\b", re.I)
CENTER = re.compile(r"\b(uptown|center city|downtown)\b", re.I)
PLACES = {
    "NoDa": (35.2470, -80.8176), "Plaza Midwood": (35.2190, -80.8090), "South End": (35.2108, -80.8565),
    "Dilworth": (35.2050, -80.8480), "Myers Park": (35.1880, -80.8300), "Eastland": (35.2020, -80.7600),
    "University City": (35.3080, -80.7430), "Ballantyne": (35.0570, -80.8480), "Steele Creek": (35.1030, -80.9470),
    "Northlake": (35.3440, -80.8860), "Hidden Valley": (35.2740, -80.7970), "Grier Heights": (35.1950, -80.8180),
    "Enderly Park": (35.2170, -80.8800), "Optimist Park": (35.2340, -80.8230), "Wesley Heights": (35.2260, -80.8660),
    "Seversville": (35.2310, -80.8610), "Camp Greene": (35.2230, -80.8840), "SouthPark": (35.1520, -80.8320),
    "Spectrum Center": (35.2251, -80.8392), "Bank of America Stadium": (35.2258, -80.8528),
    "Epicentre": (35.2255, -80.8420), "UNC Charlotte": (35.3071, -80.7352), "Northlake Mall": (35.3524, -80.8571),
    "Concord Mills": (35.3692, -80.7226), "Charlotte Douglas": (35.2144, -80.9473),
}
# Division names that are also ordinary words would match almost any headline.
GENERIC_DIVISIONS = {"north", "south", "central", "metro", "na"}
STREET = re.compile(
    r"\b(?:on|along|at|near|off)\s+((?:[A-Z][\w'.]+\s+){1,3})"
    r"(Road|Street|Avenue|Boulevard|Drive|Lane|Parkway|Highway|Place|Court|Way|Rd|St|Ave|Blvd|Dr|Ln|Pkwy|Hwy)\b")

_cache = {"data": None, "ts": 0.0}
_CACHE_TTL = 600


def _bearing(lat, lon):
    dy = (lat - UPTOWN[0]) * 111.0
    dx = (lon - UPTOWN[1]) * 91.0
    return math.degrees(math.atan2(dx, dy)) % 360


def classify(title):
    """Return (category, codes), or (None, reason) when the headline is not a new Charlotte incident."""
    if ELSEWHERE.search(title) and not re.search(r"\bin (north|south|east|west|uptown|\w+ )?Charlotte\b", title, re.I):
        return None, "Outside Charlotte"
    if not CHARLOTTE.search(title) and not any(p.lower() in title.lower() for p in PLACES):
        return None, "Outside Charlotte"
    if NOT_AN_INCIDENT.search(title):
        return None, "Not a crime incident"
    if FOLLOW_UP.search(title):
        return None, "Follow-up on an earlier case"
    for label, codes, pattern in CATEGORIES:
        if re.search(pattern, title, re.I):
            return label, codes
    return None, "Not a crime incident"


def location_hints(title, divisions):
    hints = []
    m = STREET.search(title)
    if m:
        hints.append(("street", m.group(1).strip().upper(), f"{m.group(1).strip()} {m.group(2)}"))
    lowered = title.lower()
    for name, coords in PLACES.items():
        if name.lower() in lowered:
            hints.append(("place", coords, name))
            break
    for div in divisions:
        if div.lower() not in GENERIC_DIVISIONS and re.search(rf"\b{re.escape(div)}\b", title, re.I):
            hints.append(("division", div, f"{div} division"))
            break
    if CENTER.search(title):
        hints.append(("center", None, "Uptown"))
    else:
        d = DIRECTION.search(title)
        if d and (d.group(1) or d.group(2)):
            word = ((d.group(1) or "") + (d.group(2) or "")).lower()
            hints.append(("direction", DIRECTIONS[word], f"{word} Charlotte"))
    return hints


def _location_score(inc, hints):
    """Strength of the best agreeing hint: 3 street, 2 neighbourhood or division, 1 side of town, 0 none agree."""
    best = 0
    for kind, value, _ in hints:
        if kind == "street" and value in (inc["address"] or "").upper():
            best = max(best, 3)
        elif kind == "place" and haversine_km(inc["lat"], inc["lon"], *value) <= NEAR_PLACE_KM:
            best = max(best, 2)
        elif kind == "division" and inc["division"] == value:
            best = max(best, 2)
        elif kind == "center" and haversine_km(inc["lat"], inc["lon"], *UPTOWN) <= UPTOWN_RADIUS_KM:
            best = max(best, 2)
        elif kind == "direction":
            off = abs((_bearing(inc["lat"], inc["lon"]) - value + 180) % 360 - 180)
            if off <= 67.5 and haversine_km(inc["lat"], inc["lon"], *UPTOWN) > UPTOWN_RADIUS_KM:
                best = max(best, 1)
    return best


def match_article(title, pub_date, incidents, divisions):
    """Decide what a headline lines up with. `incidents` are serious-crime reports near the date."""
    category, codes = classify(title)
    if category is None:
        return {"status": "skipped", "reason": codes}

    hints = location_hints(title, divisions)
    first_day = pub_date - timedelta(days=DAYS_BEFORE_PUBLICATION)
    pool = [
        i for i in incidents
        if i["code"] in codes and (first_day <= i["reported"] <= pub_date or first_day <= i["began"] <= pub_date)
    ]
    scored = sorted(((_location_score(i, hints), i) for i in pool), key=lambda s: -s[0])
    if hints:
        scored = [s for s in scored if s[0] > 0]

    out = {"category": category, "looked_for": ", ".join(h[2] for h in hints) or "no location in headline"}
    if not scored:
        out["status"] = "news_only"
        return out
    top_score, top = scored[0]
    tied = [i for sc, i in scored if sc == top_score]
    # Several victims of one event are filed as separate reports; treat them as one.
    one_event = all(
        i["began"] == top["began"] and haversine_km(i["lat"], i["lon"], top["lat"], top["lon"]) <= NEAR_PLACE_KM
        for i in tied
    )
    if not hints:
        # Type and date alone cannot tell one report from another, so never guess.
        out.update(status="undecided", candidates=len(scored))
    elif one_event and (len(tied) == len(scored) or top_score >= 2):
        out.update(status="matched", incident=top, confidence="Strong" if top_score >= 2 else "Likely")
    else:
        out.update(status="undecided", candidates=len(tied))
    return out


def _load(start, end):
    session = SessionLocal()
    try:
        inc_rows = (
            session.query(
                Incident.incident_report_id, Incident.nibrs_code, Incident.nibrs_offense_description,
                Incident.address, Incident.cmpd_patrol_division, Incident.latitude, Incident.longitude,
                Incident.date_reported, Incident.date_incident_began,
            )
            .filter(
                Incident.nibrs_code.in_(ALL_CODES),
                Incident.date_reported >= datetime.combine(start - timedelta(days=DAYS_BEFORE_PUBLICATION + 1), datetime.min.time()),
                Incident.latitude.isnot(None),
            )
            .all()
        )
        news_rows = (
            session.query(NewsArticle.id, NewsArticle.title, NewsArticle.url, NewsArticle.source, NewsArticle.published)
            .filter(NewsArticle.published >= datetime.combine(start, datetime.min.time()))
            .order_by(NewsArticle.published.desc())
            .all()
        )
    finally:
        session.close()
    incidents = [{
        "id": r.incident_report_id, "code": r.nibrs_code, "offense": r.nibrs_offense_description,
        "address": r.address, "division": r.cmpd_patrol_division, "lat": r.latitude, "lon": r.longitude,
        "reported": r.date_reported.date(), "began": (r.date_incident_began or r.date_reported).date(),
    } for r in inc_rows]
    return incidents, news_rows


def news_check_report():
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    latest = data_through()
    if latest is None:
        return {"data_through": None, "summary": {}, "matched": [], "news_only": [], "undecided": [], "skipped": {}}
    start = latest - timedelta(days=WINDOW_DAYS)
    incidents, articles = _load(start, latest)
    divisions = sorted({i["division"] for i in incidents if i["division"]})

    matched, news_only, undecided, too_recent, skipped = [], [], [], 0, {}
    covered = {}
    for a in articles:
        # Headlines carry the outlet as a " - Source" suffix.
        title = re.sub(r"\s+-\s+[^-]+$", "", a.title) if a.source else a.title
        # Published is UTC; a late-evening Eastern story would otherwise land on the next day.
        pub_date = (a.published - timedelta(hours=5)).date()
        result = match_article(title, pub_date, incidents, divisions)
        if result["status"] == "skipped":
            skipped[result["reason"]] = skipped.get(result["reason"], 0) + 1
            continue
        row = {
            "title": title, "url": a.url, "source": a.source, "published": str(pub_date),
            "category": result["category"], "looked_for": result["looked_for"],
        }
        if result["status"] == "matched":
            inc = result["incident"]
            row.update(confidence=result["confidence"], incident={
                "id": inc["id"], "offense": inc["offense"], "address": inc["address"],
                "division": inc["division"], "date": str(inc["began"]),
            })
            matched.append(row)
            covered.setdefault(inc["id"], 0)
            covered[inc["id"]] += 1
        elif result["status"] == "undecided":
            row["candidates"] = result["candidates"]
            undecided.append(row)
        elif pub_date > latest - timedelta(days=PUBLICATION_LAG_DAYS - 1):
            too_recent += 1
        else:
            news_only.append(row)

    murders = [i for i in incidents if i["code"] == "09A" and i["reported"] >= start]
    murders_covered = sum(1 for i in murders if i["id"] in covered)
    judged = len(matched) + len(news_only) + len(undecided)
    report = {
        "data_through": str(latest),
        "window_days": WINDOW_DAYS,
        "newest_article": str((articles[0].published - timedelta(hours=5)).date()) if articles else None,
        "summary": {
            "articles": len(articles),
            "incident_stories": judged,
            "matched": len(matched),
            "news_only": len(news_only),
            "undecided": len(undecided),
            "too_recent": too_recent,
            "match_rate": round(len(matched) / judged * 100) if judged else None,
            "distinct_reports": len(covered),
            "murders": len(murders),
            "murders_covered": murders_covered,
        },
        "matched": matched,
        "news_only": news_only,
        "undecided": undecided,
        "skipped": skipped,
    }
    _cache["data"] = report
    _cache["ts"] = now
    return report


def reset_cache():
    _cache["ts"] = 0.0
