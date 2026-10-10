from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal

CHARLOTTE_VENUES = {
    "Bank of America Stadium": {"lat": 35.2258, "lon": -80.8528, "radius_km": 0.8,
        "teams": ["Carolina Panthers", "Charlotte FC"]},
    "Spectrum Center": {"lat": 35.2251, "lon": -80.8392, "radius_km": 0.5,
        "teams": ["Charlotte Hornets"]},
    "PNC Music Pavilion": {"lat": 35.3381, "lon": -80.7407, "radius_km": 0.6,
        "teams": []},
    "Bojangles Coliseum": {"lat": 35.2100, "lon": -80.7980, "radius_km": 0.4,
        "teams": ["Charlotte Checkers"]},
    "Charlotte Convention Center": {"lat": 35.2240, "lon": -80.8450, "radius_km": 0.4,
        "teams": []},
    "Truist Field": {"lat": 35.2270, "lon": -80.8500, "radius_km": 0.4,
        "teams": ["Charlotte Knights"]},
    "Memorial Stadium": {"lat": 35.2170, "lon": -80.8070, "radius_km": 0.4,
        "teams": ["Charlotte Independence"]},
}


def venue_crime_analysis(days_back=365, nibrs_code=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            Incident.latitude, Incident.longitude,
            Incident.date_reported, Incident.nibrs_code,
        ).filter(
            Incident.latitude.isnot(None),
            Incident.date_reported >= cutoff,
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        incidents = q.all()

        import numpy as np
        results = []
        for name, venue in CHARLOTTE_VENUES.items():
            nearby = []
            for inc in incidents:
                dlat = np.radians(inc.latitude - venue["lat"])
                dlon = np.radians(inc.longitude - venue["lon"])
                a = np.sin(dlat/2)**2 + np.cos(np.radians(venue["lat"])) * np.cos(np.radians(inc.latitude)) * np.sin(dlon/2)**2
                dist = 6371 * 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
                if dist <= venue["radius_km"]:
                    nearby.append(inc)

            if not nearby:
                continue

            from collections import Counter
            hour_counts = Counter()
            dow_counts = Counter()
            type_counts = Counter()
            for inc in nearby:
                if inc.date_reported:
                    hour_counts[inc.date_reported.hour] += 1
                    dow_counts[inc.date_reported.weekday()] += 1
                if inc.nibrs_code:
                    type_counts[inc.nibrs_code] += 1

            peak_hour = hour_counts.most_common(1)[0] if hour_counts else (None, 0)
            peak_dow = dow_counts.most_common(1)[0] if dow_counts else (None, 0)
            dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

            results.append({
                "venue": name,
                "lat": venue["lat"],
                "lon": venue["lon"],
                "teams": venue["teams"],
                "total_incidents": len(nearby),
                "daily_avg": round(len(nearby) / days_back, 1),
                "peak_hour": peak_hour[0],
                "peak_day": dow_names.get(peak_dow[0], "?"),
                "top_types": [{"code": t[0], "count": t[1]} for t in type_counts.most_common(5)],
            })

        results.sort(key=lambda r: r["total_incidents"], reverse=True)
        return results
    finally:
        session.close()
