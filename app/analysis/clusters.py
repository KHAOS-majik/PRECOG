import numpy as np
from sklearn.cluster import DBSCAN
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal


def find_clusters(days_back=30, eps_km=0.3, min_samples=5, nibrs_code=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            Incident.latitude, Incident.longitude,
            Incident.nibrs_code, Incident.address, Incident.date_reported,
        ).filter(
            Incident.latitude.isnot(None),
            Incident.longitude.isnot(None),
            Incident.date_reported >= cutoff,
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.all()

        if len(rows) < min_samples:
            return []

        coords = np.array([[r.latitude, r.longitude] for r in rows])
        coords_rad = np.radians(coords)

        eps_rad = eps_km / 6371.0
        db = DBSCAN(eps=eps_rad, min_samples=min_samples, metric="haversine")
        labels = db.fit_predict(coords_rad)

        clusters = {}
        for i, label in enumerate(labels):
            if label == -1:
                continue
            if label not in clusters:
                clusters[label] = {
                    "lats": [], "lons": [], "types": [], "addresses": set(), "dates": [],
                }
            clusters[label]["lats"].append(coords[i][0])
            clusters[label]["lons"].append(coords[i][1])
            clusters[label]["types"].append(rows[i].nibrs_code)
            if rows[i].address:
                clusters[label]["addresses"].add(rows[i].address)
            if rows[i].date_reported:
                clusters[label]["dates"].append(rows[i].date_reported)

        result = []
        for label, data in clusters.items():
            from collections import Counter
            type_counts = Counter(data["types"])
            top_types = type_counts.most_common(3)
            dates_sorted = sorted(data["dates"])

            result.append({
                "cluster_id": int(label),
                "center_lat": round(float(np.mean(data["lats"])), 6),
                "center_lon": round(float(np.mean(data["lons"])), 6),
                "incident_count": len(data["lats"]),
                "radius_m": round(float(_haversine_max_radius(data["lats"], data["lons"])), 0),
                "top_types": [{"code": t[0], "count": t[1]} for t in top_types],
                "unique_addresses": len(data["addresses"]),
                "first_date": dates_sorted[0].isoformat() if dates_sorted else None,
                "last_date": dates_sorted[-1].isoformat() if dates_sorted else None,
            })

        result.sort(key=lambda c: c["incident_count"], reverse=True)
        return result[:30]
    finally:
        session.close()


def _haversine_max_radius(lats, lons):
    center_lat = np.mean(lats)
    center_lon = np.mean(lons)
    max_dist = 0
    for lat, lon in zip(lats, lons):
        dlat = np.radians(lat - center_lat)
        dlon = np.radians(lon - center_lon)
        a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(center_lat)) * np.cos(np.radians(lat)) * np.sin(dlon / 2) ** 2
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
        dist = 6371000 * c
        max_dist = max(max_dist, dist)
    return max_dist


def find_emerging_clusters(recent_days=14, baseline_days=90, eps_km=0.3, min_samples=5, nibrs_code=None):
    recent = find_clusters(recent_days, eps_km, min_samples, nibrs_code=nibrs_code)
    baseline = find_clusters(baseline_days, eps_km, min_samples, nibrs_code=nibrs_code)

    baseline_density = {}
    for bc in baseline:
        key = (round(bc["center_lat"], 3), round(bc["center_lon"], 3))
        baseline_density[key] = bc["incident_count"] / baseline_days

    emerging = []
    for rc in recent:
        key = (round(rc["center_lat"], 3), round(rc["center_lon"], 3))
        recent_rate = rc["incident_count"] / recent_days

        matched_baseline_rate = 0
        for bk, br in baseline_density.items():
            dlat = abs(key[0] - bk[0])
            dlon = abs(key[1] - bk[1])
            if dlat < 0.005 and dlon < 0.005:
                matched_baseline_rate = max(matched_baseline_rate, br)

        if matched_baseline_rate > 0:
            change_ratio = recent_rate / matched_baseline_rate
        else:
            change_ratio = float('inf')

        if change_ratio > 1.5 or matched_baseline_rate == 0:
            rc["daily_rate"] = round(recent_rate, 2)
            rc["baseline_rate"] = round(matched_baseline_rate, 2)
            rc["change_ratio"] = round(change_ratio, 2) if change_ratio != float('inf') else None
            rc["is_new"] = matched_baseline_rate == 0
            emerging.append(rc)

    emerging.sort(key=lambda e: e["incident_count"], reverse=True)
    return emerging[:20]
