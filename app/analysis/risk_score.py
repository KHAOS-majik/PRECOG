import time
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal
from app.analysis.model_cache import get_or_train

_NPA_CENTERS = {"data": {}, "ts": 0}
_NPA_CENTERS_TTL = 3600


def _get_npa_centers():
    now = time.time()
    if _NPA_CENTERS["data"] and (now - _NPA_CENTERS["ts"]) < _NPA_CENTERS_TTL:
        return _NPA_CENTERS["data"]
    session = SessionLocal()
    try:
        rows = (
            session.query(
                Incident.npa,
                func.avg(Incident.latitude).label("lat"),
                func.avg(Incident.longitude).label("lon"),
                func.count().label("c"),
            )
            .filter(Incident.npa.isnot(None), Incident.latitude.isnot(None))
            .group_by(Incident.npa)
            .having(func.count() >= 10)
            .all()
        )
        data = {r.npa: (round(float(r.lat), 6), round(float(r.lon), 6)) for r in rows}
        _NPA_CENTERS["data"] = data
        _NPA_CENTERS["ts"] = now
        return data
    finally:
        session.close()


def _anomaly_scores(nibrs_code=None, days_back=7):
    from app.analysis.anomaly import detect_anomalies
    anomalies = detect_anomalies(days_back=days_back, threshold_sigma=1.5, nibrs_code=nibrs_code)
    npa_scores = {}
    for a in anomalies:
        npa = a["npa"]
        score = min(100, abs(a["z_score"]) * 25)
        if npa not in npa_scores or score > npa_scores[npa]:
            npa_scores[npa] = round(score, 1)
    return npa_scores


def _cluster_scores(nibrs_code=None, days_back=90):
    from app.analysis.clusters import find_emerging_clusters
    recent = min(max(days_back // 6, 7), 30)
    clusters = find_emerging_clusters(recent_days=recent, baseline_days=max(days_back, 60), nibrs_code=nibrs_code)
    centers = _get_npa_centers()
    npa_scores = {}
    for cl in clusters:
        cl_lat, cl_lon = cl["center_lat"], cl["center_lon"]
        best_npa, best_dist = None, float("inf")
        for npa, (lat, lon) in centers.items():
            dlat = (lat - cl_lat) * 111
            dlon = (lon - cl_lon) * 85
            dist = (dlat * dlat + dlon * dlon) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_npa = npa
        if best_npa and best_dist < 2.0:
            score = min(100, cl["incident_count"] * 5)
            if best_npa not in npa_scores or score > npa_scores[best_npa]:
                npa_scores[best_npa] = round(score, 1)
    return npa_scores


def _repeat_scores(days_back=90, nibrs_code=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            Incident.npa,
            Incident.address,
            func.count().label("c"),
        ).filter(
            Incident.date_reported >= cutoff,
            Incident.npa.isnot(None),
            Incident.address.isnot(None),
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by(Incident.npa, Incident.address).having(func.count() >= 3).all()
        npa_repeat_count = defaultdict(int)
        for r in rows:
            npa_repeat_count[r.npa] += 1
        max_repeats = max(npa_repeat_count.values()) if npa_repeat_count else 1
        return {npa: round(min(100, (c / max_repeats) * 100), 1) for npa, c in npa_repeat_count.items()}
    finally:
        session.close()


def _311_scores(days_back=90):
    from app.analysis.features import get_311_density_by_npa
    density = get_311_density_by_npa(days_back)
    if not density:
        return {}
    max_val = max(density.values())
    if max_val == 0:
        return {}
    return {npa: round(min(100, (c / max_val) * 100), 1) for npa, c in density.items()}


def _recency_scores(nibrs_code=None, days_back=90):
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(days=max(days_back, 30))
        q = session.query(
            Incident.npa,
            func.max(Incident.date_reported).label("latest"),
        ).filter(Incident.npa.isnot(None), Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by(Incident.npa).all()
        scores = {}
        for r in rows:
            if r.latest:
                days_ago = (now - r.latest).days
                scores[r.npa] = round(max(0, 100 - days_ago * 10), 1)
        return scores
    finally:
        session.close()


def _forecast_scores(nibrs_code=None, days_back=365):
    if nibrs_code:
        return {}
    from app.analysis.forecaster import forecast_by_npa
    try:
        npa_forecasts = forecast_by_npa(days_back=days_back, forecast_days=7, top_n=40)
    except Exception:
        return {}
    scores = {}
    for nf in npa_forecasts:
        hist_avg = nf["historical_avg"]
        fc_avg = nf["forecast_avg"]
        if hist_avg > 0:
            ratio = fc_avg / hist_avg
            score = min(100, max(0, (ratio - 0.8) * 250))
        else:
            score = 50
        scores[nf["npa"]] = round(score, 1)
    return scores


WEIGHTS = {
    "forecast": 0.25,
    "anomaly": 0.20,
    "cluster": 0.15,
    "repeat": 0.15,
    "disorder_311": 0.15,
    "recency": 0.10,
}


def compute_risk_scores(nibrs_code=None, days_back=90):
    cache_key = f"risk_scores_composite_{nibrs_code or 'all'}_{days_back}"

    def build():
        forecast_s = _forecast_scores(nibrs_code=nibrs_code)
        anomaly_s = _anomaly_scores(nibrs_code=nibrs_code, days_back=min(days_back, 14))
        cluster_s = _cluster_scores(nibrs_code=nibrs_code, days_back=days_back)
        repeat_s = _repeat_scores(days_back=days_back, nibrs_code=nibrs_code)
        disorder_s = {} if nibrs_code else _311_scores(days_back=days_back)
        recency_s = _recency_scores(nibrs_code=nibrs_code, days_back=days_back)
        centers = _get_npa_centers()

        all_npas = set()
        for d in [forecast_s, anomaly_s, cluster_s, repeat_s, disorder_s, recency_s]:
            all_npas.update(d.keys())

        results = []
        for npa in all_npas:
            components = {
                "forecast": forecast_s.get(npa, 0),
                "anomaly": anomaly_s.get(npa, 0),
                "cluster": cluster_s.get(npa, 0),
                "repeat": repeat_s.get(npa, 0),
                "disorder_311": disorder_s.get(npa, 0),
                "recency": recency_s.get(npa, 0),
            }
            w = WEIGHTS
            if nibrs_code:
                w = {k: v for k, v in WEIGHTS.items() if k != "disorder_311"}
                total_w = sum(w.values())
                w = {k: v / total_w for k, v in w.items()}
            composite = sum(components[k] * w.get(k, 0) for k in components)
            composite = round(min(100, max(0, composite)), 1)

            top_factor = max(components, key=components.get)

            center = centers.get(npa)
            if not center:
                continue
            if composite < 1:
                continue

            results.append({
                "npa": npa,
                "risk_score": composite,
                "components": components,
                "top_factor": top_factor,
                "latitude": center[0],
                "longitude": center[1],
                "narrative": _natural_language(npa, composite, components, top_factor),
            })

        results.sort(key=lambda r: r["risk_score"], reverse=True)
        meta = {"results": results, "n_npas": len(results)}
        return results, meta

    results, meta = get_or_train(cache_key, build, max_age_hours=1)
    return meta.get("results", results) if isinstance(results, list) else results


def _risk_level(score):
    if score >= 75:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "ELEVATED"
    return "LOW"


def _natural_language(npa, score, components, top_factor):
    level = _risk_level(score)
    factor_phrases = {
        "forecast": "forecast trends are above the historical baseline",
        "anomaly": "a statistical anomaly was detected in recent days",
        "cluster": "an emerging crime cluster has formed nearby",
        "repeat": "repeat victimization hotspots are concentrated here",
        "disorder_311": "311 disorder complaints are elevated",
        "recency": "incidents occurred very recently",
    }

    active = [(k, v) for k, v in sorted(components.items(), key=lambda x: -x[1]) if v > 15]
    if not active:
        return f"Area {npa} risk is {level} ({score}/100) with no strongly active signals."

    phrases = [factor_phrases.get(k, k) for k, _ in active[:3]]
    if len(phrases) == 1:
        detail = phrases[0]
    elif len(phrases) == 2:
        detail = f"{phrases[0]} and {phrases[1]}"
    else:
        detail = f"{phrases[0]}, {phrases[1]}, and {phrases[2]}"

    return f"Area {npa} risk is {level} ({score}/100): {detail}."


def explain_npa_risk(npa):
    scores = compute_risk_scores()
    for s in scores:
        if s["npa"] == npa:
            components = s["components"]
            explanations = []
            for k, v in sorted(components.items(), key=lambda x: -x[1]):
                if v > 10:
                    label = {
                        "forecast": "Forecast trend above baseline",
                        "anomaly": "Statistical anomaly detected",
                        "cluster": "Emerging crime cluster nearby",
                        "repeat": "Repeat victimization hotspots",
                        "disorder_311": "High 311 disorder complaints",
                        "recency": "Very recent incidents",
                    }.get(k, k)
                    explanations.append({"factor": k, "label": label, "score": v, "weight": WEIGHTS[k]})

            return {
                "npa": npa,
                "risk_score": s["risk_score"],
                "risk_level": _risk_level(s["risk_score"]),
                "top_factor": s["top_factor"],
                "latitude": s["latitude"],
                "longitude": s["longitude"],
                "explanations": explanations,
                "narrative": s.get("narrative"),
            }
    return {"npa": npa, "risk_score": 0, "risk_level": "LOW", "explanations": [], "narrative": "No data available for this NPA.", "error": "NPA not found"}
