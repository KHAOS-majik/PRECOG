import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal
from app.analysis.model_cache import get_or_train


def detect_temporal_patterns(days_back=180, min_occurrences=3, nibrs_code=None):
    """Find crimes that recur at the same location on a regular schedule."""
    cache_key = f"temporal_patterns_{days_back}_{nibrs_code or 'all'}"

    def build():
        session = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            q = session.query(
                Incident.address, Incident.npa,
                Incident.nibrs_code, Incident.nibrs_offense_description,
                Incident.date_reported, Incident.latitude, Incident.longitude,
            ).filter(
                Incident.date_reported >= cutoff,
                Incident.address.isnot(None),
            )
            if nibrs_code:
                q = q.filter(Incident.nibrs_code == nibrs_code)
            rows = q.order_by(Incident.address, Incident.date_reported).all()

            by_address = defaultdict(list)
            for r in rows:
                by_address[r.address].append({
                    "npa": r.npa,
                    "code": r.nibrs_code,
                    "desc": r.nibrs_offense_description,
                    "dt": r.date_reported,
                    "lat": r.latitude,
                    "lon": r.longitude,
                })

            patterns = []
            for addr, incidents in by_address.items():
                if len(incidents) < min_occurrences:
                    continue

                dow_counts = Counter(i["dt"].weekday() for i in incidents if i["dt"])
                hour_counts = Counter(i["dt"].hour for i in incidents if i["dt"])

                top_dow, top_dow_count = dow_counts.most_common(1)[0]
                dow_ratio = top_dow_count / len(incidents)
                top_hour, top_hour_count = hour_counts.most_common(1)[0]
                hour_ratio = top_hour_count / len(incidents)

                type_counts = Counter(i["code"] for i in incidents)
                top_type, top_type_count = type_counts.most_common(1)[0]
                type_desc = next((i["desc"] for i in incidents if i["code"] == top_type), top_type)

                if dow_ratio >= 0.5 and len(incidents) >= min_occurrences:
                    dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    confidence = round(dow_ratio * 100, 0)
                    last = incidents[-1]

                    pattern_desc = f"{type_desc} on {dow_names[top_dow]}s"
                    if hour_ratio >= 0.4:
                        h = top_hour
                        ampm = "AM" if h < 12 else "PM"
                        h12 = h % 12 or 12
                        pattern_desc += f" around {h12}{ampm}"

                    patterns.append({
                        "address": addr,
                        "npa": last["npa"],
                        "latitude": last["lat"],
                        "longitude": last["lon"],
                        "pattern": pattern_desc,
                        "day_of_week": dow_names[top_dow],
                        "peak_hour": top_hour if hour_ratio >= 0.4 else None,
                        "incident_count": len(incidents),
                        "confidence": confidence,
                        "top_type": type_desc,
                        "nibrs_code": top_type,
                        "last_date": last["dt"].strftime("%Y-%m-%d") if last["dt"] else None,
                    })

            patterns.sort(key=lambda p: (p["confidence"], p["incident_count"]), reverse=True)
            return patterns[:50], {"patterns": patterns[:50]}
        finally:
            session.close()

    patterns, meta = get_or_train(cache_key, build, max_age_hours=3)
    return meta.get("patterns", patterns)


def detect_geographic_drift(days_back=180, min_cluster_size=10, nibrs_code=None):
    """Track if crime cluster centers are moving over time."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(days=days_back)
        mid = now - timedelta(days=days_back // 2)

        tq = session.query(Incident.npa, func.count().label("c")).filter(
            Incident.date_reported >= cutoff, Incident.npa.isnot(None), Incident.latitude.isnot(None)
        )
        if nibrs_code:
            tq = tq.filter(Incident.nibrs_code == nibrs_code)
        top_npas = tq.group_by(Incident.npa).having(func.count() >= min_cluster_size * 2).order_by(func.count().desc()).limit(30).all()

        drifts = []
        for npa_row in top_npas:
            npa = npa_row.npa

            eq = session.query(
                func.avg(Incident.latitude).label("lat"),
                func.avg(Incident.longitude).label("lon"),
                func.count().label("c"),
            ).filter(
                Incident.npa == npa,
                Incident.date_reported >= cutoff,
                Incident.date_reported < mid,
                Incident.latitude.isnot(None),
            )
            if nibrs_code:
                eq = eq.filter(Incident.nibrs_code == nibrs_code)
            early = eq.first()

            rq = session.query(
                func.avg(Incident.latitude).label("lat"),
                func.avg(Incident.longitude).label("lon"),
                func.count().label("c"),
            ).filter(
                Incident.npa == npa,
                Incident.date_reported >= mid,
                Incident.latitude.isnot(None),
            )
            if nibrs_code:
                rq = rq.filter(Incident.nibrs_code == nibrs_code)
            recent = rq.first()

            if not early or not recent or not early.lat or not recent.lat:
                continue
            if early.c < min_cluster_size or recent.c < min_cluster_size:
                continue

            dlat = float(recent.lat) - float(early.lat)
            dlon = float(recent.lon) - float(early.lon)
            dist_km = np.sqrt((dlat * 111) ** 2 + (dlon * 85) ** 2)

            if dist_km < 0.05:
                continue

            if abs(dlat) > abs(dlon):
                direction = "north" if dlat > 0 else "south"
            else:
                direction = "east" if dlon > 0 else "west"

            drifts.append({
                "npa": npa,
                "direction": direction,
                "distance_km": round(dist_km, 2),
                "early_center": {"lat": round(float(early.lat), 5), "lon": round(float(early.lon), 5)},
                "recent_center": {"lat": round(float(recent.lat), 5), "lon": round(float(recent.lon), 5)},
                "early_count": early.c,
                "recent_count": recent.c,
                "volume_change_pct": round((recent.c - early.c) / max(early.c, 1) * 100, 1),
            })

        drifts.sort(key=lambda d: d["distance_km"], reverse=True)
        return drifts[:20]
    finally:
        session.close()


def detect_mo_patterns(days_back=90, min_group=5, nibrs_code=None):
    """Group incidents by type + place_type + time_of_week to find operationally similar patterns."""
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            Incident.nibrs_code, Incident.nibrs_offense_description,
            Incident.place_type_description, Incident.date_reported,
            Incident.npa, Incident.address,
        ).filter(
            Incident.date_reported >= cutoff,
            Incident.nibrs_code.isnot(None),
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.all()

        groups = defaultdict(list)
        for r in rows:
            if not r.date_reported:
                continue
            dow = r.date_reported.weekday()
            time_slot = "night" if r.date_reported.hour < 6 else "morning" if r.date_reported.hour < 12 else "afternoon" if r.date_reported.hour < 18 else "evening"
            is_weekend = dow >= 5
            time_label = f"{'weekend' if is_weekend else 'weekday'} {time_slot}"
            place = r.place_type_description or "Unknown"

            key = (r.nibrs_code, place, time_label)
            groups[key].append({
                "desc": r.nibrs_offense_description,
                "npa": r.npa,
                "address": r.address,
                "date": r.date_reported.strftime("%Y-%m-%d"),
            })

        mo_patterns = []
        for (code, place, time_label), incidents in groups.items():
            if len(incidents) < min_group:
                continue

            npa_counts = Counter(i["npa"] for i in incidents if i["npa"])
            top_npas = [{"npa": n, "count": c} for n, c in npa_counts.most_common(3)]

            mo_patterns.append({
                "nibrs_code": code,
                "description": incidents[0]["desc"],
                "place_type": place,
                "time_pattern": time_label,
                "count": len(incidents),
                "top_npas": top_npas,
                "recent_date": incidents[-1]["date"],
            })

        mo_patterns.sort(key=lambda m: m["count"], reverse=True)
        return mo_patterns[:30]
    finally:
        session.close()


def get_all_patterns(days_back=180, nibrs_code=None):
    """Combined pattern detection: temporal, geographic drift, and MO."""
    temporal = detect_temporal_patterns(days_back, min_occurrences=4, nibrs_code=nibrs_code)
    drift = detect_geographic_drift(days_back, nibrs_code=nibrs_code)
    mo = detect_mo_patterns(min(days_back, 90), nibrs_code=nibrs_code)

    return {
        "temporal_patterns": temporal[:15],
        "geographic_drift": drift[:10],
        "mo_patterns": mo[:15],
        "summary": {
            "temporal_count": len(temporal),
            "drift_count": len(drift),
            "mo_count": len(mo),
        },
    }
