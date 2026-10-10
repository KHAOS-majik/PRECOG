from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal
from app.analysis.model_cache import get_or_train

PROPERTY_CODES = {"23F", "23H", "23C", "23G", "23D", "240", "220", "290", "250"}
VIOLENT_CODES = {"13A", "13B", "120", "09A", "09B", "11A", "64A"}

CATEGORY_MAP = {}
for c in PROPERTY_CODES:
    CATEGORY_MAP[c] = "property"
for c in VIOLENT_CODES:
    CATEGORY_MAP[c] = "violent"


def compute_transitions(days_back=365, window_days=30):
    """Compute crime type transition probabilities at locations.

    For each address, look at sequences of incidents within window_days of each other.
    Build a transition matrix: P(type_B | type_A occurred within window_days).
    Focus on property → violent escalation.
    """
    cache_key = f"transitions_{days_back}_{window_days}"

    def build():
        session = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            rows = (
                session.query(
                    Incident.address,
                    Incident.npa,
                    Incident.nibrs_code,
                    Incident.date_reported,
                )
                .filter(
                    Incident.date_reported >= cutoff,
                    Incident.address.isnot(None),
                    Incident.nibrs_code.isnot(None),
                )
                .order_by(Incident.address, Incident.date_reported)
                .all()
            )

            by_address = defaultdict(list)
            for r in rows:
                cat = CATEGORY_MAP.get(r.nibrs_code)
                if cat:
                    by_address[r.address].append({
                        "npa": r.npa,
                        "code": r.nibrs_code,
                        "cat": cat,
                        "date": r.date_reported,
                    })

            transitions = defaultdict(int)
            total_from = defaultdict(int)

            for addr, incidents in by_address.items():
                if len(incidents) < 2:
                    continue
                for i, a in enumerate(incidents):
                    for j in range(i + 1, len(incidents)):
                        b = incidents[j]
                        gap = (b["date"] - a["date"]).days
                        if gap > window_days:
                            break
                        pair = (a["cat"], b["cat"])
                        transitions[pair] += 1
                        total_from[a["cat"]] += 1

            probs = {}
            for (from_cat, to_cat), count in transitions.items():
                total = total_from[from_cat]
                if total > 0:
                    probs[f"{from_cat}_to_{to_cat}"] = {
                        "count": count,
                        "total": total,
                        "probability": round(count / total, 4),
                    }

            return probs, {"transitions": probs}
        finally:
            session.close()

    probs, meta = get_or_train(cache_key, build, max_age_hours=6)
    if isinstance(probs, dict) and "transitions" not in probs:
        return probs
    return meta.get("transitions", probs)


def find_escalation_locations(days_back=180, window_days=30, min_property=2):
    """Find locations where property crime preceded violent crime."""
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        rows = (
            session.query(
                Incident.address,
                Incident.npa,
                Incident.nibrs_code,
                Incident.nibrs_offense_description,
                Incident.date_reported,
                Incident.latitude,
                Incident.longitude,
            )
            .filter(
                Incident.date_reported >= cutoff,
                Incident.address.isnot(None),
                Incident.nibrs_code.isnot(None),
            )
            .order_by(Incident.address, Incident.date_reported)
            .all()
        )

        by_address = defaultdict(list)
        for r in rows:
            by_address[r.address].append({
                "npa": r.npa,
                "code": r.nibrs_code,
                "desc": r.nibrs_offense_description,
                "cat": CATEGORY_MAP.get(r.nibrs_code, "other"),
                "date": r.date_reported,
                "lat": r.latitude,
                "lon": r.longitude,
            })

        escalations = []
        for addr, incidents in by_address.items():
            property_incidents = [i for i in incidents if i["cat"] == "property"]
            violent_incidents = [i for i in incidents if i["cat"] == "violent"]

            if len(property_incidents) < min_property or not violent_incidents:
                continue

            for vi in violent_incidents:
                preceding = [
                    p for p in property_incidents
                    if 0 < (vi["date"] - p["date"]).days <= window_days
                ]
                if len(preceding) >= min_property:
                    escalations.append({
                        "address": addr,
                        "npa": vi["npa"],
                        "latitude": vi["lat"],
                        "longitude": vi["lon"],
                        "property_count": len(preceding),
                        "property_types": list(set(p["desc"] for p in preceding)),
                        "violent_type": vi["desc"],
                        "violent_code": vi["code"],
                        "violent_date": vi["date"].strftime("%Y-%m-%d") if vi["date"] else None,
                        "days_since_first_property": (vi["date"] - preceding[0]["date"]).days if preceding else 0,
                    })

        escalations.sort(key=lambda e: e["property_count"], reverse=True)

        at_risk = []
        now = datetime.utcnow()
        for addr, incidents in by_address.items():
            recent_property = [
                i for i in incidents
                if i["cat"] == "property" and (now - i["date"]).days <= window_days
            ]
            has_recent_violent = any(
                i["cat"] == "violent" and (now - i["date"]).days <= window_days
                for i in incidents
            )
            if len(recent_property) >= min_property and not has_recent_violent:
                last = recent_property[-1]
                at_risk.append({
                    "address": addr,
                    "npa": last["npa"],
                    "latitude": last["lat"],
                    "longitude": last["lon"],
                    "property_count": len(recent_property),
                    "property_types": list(set(p["desc"] for p in recent_property)),
                    "last_property_date": last["date"].strftime("%Y-%m-%d") if last["date"] else None,
                    "risk_level": "high" if len(recent_property) >= 4 else "medium",
                })

        at_risk.sort(key=lambda r: r["property_count"], reverse=True)

        return {
            "past_escalations": escalations[:20],
            "at_risk_locations": at_risk[:20],
            "summary": {
                "total_escalations": len(escalations),
                "total_at_risk": len(at_risk),
                "high_risk_count": sum(1 for r in at_risk if r["risk_level"] == "high"),
            },
        }
    finally:
        session.close()
