from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal
from app.ingestion.cmpd import ingest
from app.ingestion.news import ingest_news
from app.ingestion.weather import ingest_weather
from app.ingestion.service311 import ingest_311
from app.models.news import NewsArticle, init_news_db
from app.models.service_request import ServiceRequest
from app.analysis.hotspots import compute_hotspots, get_top_hotspot_areas
from app.analysis.trends import daily_counts, hourly_pattern, day_of_week_pattern, forecast_daily
from app.analysis.repeat_victimization import get_repeat_locations, get_weather_crime_correlation
from app.analysis.forecaster import forecast_ml, forecast_by_npa
from app.analysis.risk_score import compute_risk_scores, explain_npa_risk
from app.analysis.anomaly import detect_anomalies, get_npa_anomaly_summary
from app.analysis.clusters import find_clusters, find_emerging_clusters
from app.analysis.proximity import proximity_analysis
from app.analysis.clearance import clearance_rates, clearance_time_analysis
from app.analysis.comparison import what_changed
from app.analysis.demographics import per_capita_rates
from app.analysis.events import venue_crime_analysis
from app.analysis.briefing import generate_briefing
from app.analysis.type_forecaster import forecast_by_type, type_forecast_by_npa
from app.analysis.type_transition import compute_transitions, find_escalation_locations
from app.analysis.events_calendar import get_event_impact, get_upcoming_events
from app.analysis.patterns import get_all_patterns
from app.ingestion.reddit import ingest_reddit
from app.ingestion.bluesky import ingest_bluesky
from app.models.social import SocialPost, init_social_db

router = APIRouter()


@router.get("/incidents")
def list_incidents(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    days_back: Optional[int] = Query(None, ge=1, le=365),
    nibrs_code: Optional[str] = None,
    division_id: Optional[str] = None,
    npa: Optional[str] = None,
):
    session = SessionLocal()
    try:
        q = session.query(Incident)
        if days_back:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            q = q.filter(Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        if division_id:
            q = q.filter(Incident.division_id == division_id)
        if npa:
            q = q.filter(Incident.npa == npa)
        q = q.order_by(Incident.date_reported.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": [
                {
                    "incident_report_id": r.incident_report_id,
                    "date_reported": r.date_reported.isoformat() if r.date_reported else None,
                    "address": r.address,
                    "nibrs_code": r.nibrs_code,
                    "nibrs_offense_description": r.nibrs_offense_description,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "division_id": r.division_id,
                    "clearance_status": r.clearance_status,
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@router.get("/incidents/{report_id}")
def get_incident(report_id: str):
    session = SessionLocal()
    try:
        incident = session.query(Incident).filter_by(
            incident_report_id=report_id
        ).first()
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        return {
            c.name: getattr(incident, c.name)
            for c in Incident.__table__.columns
        }
    finally:
        session.close()


@router.get("/stats/by-type")
def stats_by_type():
    session = SessionLocal()
    try:
        rows = (
            session.query(
                Incident.nibrs_code,
                Incident.nibrs_offense_description,
                func.count().label("count"),
            )
            .group_by(Incident.nibrs_code, Incident.nibrs_offense_description)
            .order_by(func.count().desc())
            .all()
        )
        return [
            {"nibrs_code": r.nibrs_code, "description": r.nibrs_offense_description, "count": r.count}
            for r in rows
        ]
    finally:
        session.close()


@router.get("/stats/by-division")
def stats_by_division(
    days_back: Optional[int] = Query(None, ge=1, le=1825),
    nibrs_code: Optional[str] = None,
):
    session = SessionLocal()
    try:
        q = session.query(
            Incident.division_id,
            func.count().label("count"),
        )
        if days_back:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            q = q.filter(Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by(Incident.division_id).order_by(func.count().desc()).all()
        return [{"division_id": r.division_id, "count": r.count} for r in rows]
    finally:
        session.close()


@router.get("/analysis/area/{npa}")
def get_area_summary(
    npa: str,
    days_back: int = Query(30, ge=7, le=365),
):
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        cur_cutoff = now - timedelta(days=days_back)
        prev_cutoff = now - timedelta(days=days_back * 2)

        cur_count = session.query(func.count(Incident.id)).filter(
            Incident.npa == npa, Incident.date_reported >= cur_cutoff
        ).scalar() or 0
        prev_count = session.query(func.count(Incident.id)).filter(
            Incident.npa == npa,
            Incident.date_reported >= prev_cutoff,
            Incident.date_reported < cur_cutoff,
        ).scalar() or 0

        delta = cur_count - prev_count
        pct = round(delta / max(prev_count, 1) * 100, 1) if prev_count else None
        trend = "stable"
        if prev_count:
            if cur_count > prev_count * 1.1:
                trend = "increasing"
            elif cur_count < prev_count * 0.9:
                trend = "decreasing"

        top_types = (
            session.query(
                Incident.nibrs_code,
                Incident.nibrs_offense_description,
                func.count().label("c"),
            )
            .filter(Incident.npa == npa, Incident.date_reported >= cur_cutoff)
            .group_by(Incident.nibrs_code, Incident.nibrs_offense_description)
            .order_by(func.count().desc())
            .limit(5)
            .all()
        )

        recent = (
            session.query(Incident)
            .filter(Incident.npa == npa, Incident.date_reported >= cur_cutoff)
            .order_by(Incident.date_reported.desc())
            .limit(10)
            .all()
        )

        center = session.query(
            func.avg(Incident.latitude).label("lat"),
            func.avg(Incident.longitude).label("lon"),
        ).filter(Incident.npa == npa, Incident.latitude.isnot(None)).first()

        return {
            "npa": npa,
            "current_count": cur_count,
            "previous_count": prev_count,
            "delta": delta,
            "pct_change": pct,
            "trend": trend,
            "days_back": days_back,
            "latitude": float(center.lat) if center and center.lat else None,
            "longitude": float(center.lon) if center and center.lon else None,
            "top_types": [
                {"nibrs_code": t.nibrs_code, "description": t.nibrs_offense_description, "count": t.c}
                for t in top_types
            ],
            "recent_incidents": [
                {
                    "date": r.date_reported.isoformat() if r.date_reported else None,
                    "address": r.address,
                    "nibrs_offense_description": r.nibrs_offense_description,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                }
                for r in recent
            ],
        }
    finally:
        session.close()


@router.get("/analysis/hotspots")
def get_hotspots(
    days_back: int = Query(90, ge=1, le=365),
    nibrs_code: Optional[str] = None,
    grid_size: int = Query(50, ge=20, le=100),
):
    return compute_hotspots(days_back=days_back, nibrs_code=nibrs_code, grid_size=grid_size)


@router.get("/analysis/top-areas")
def get_top_areas(
    days_back: int = Query(90, ge=1, le=365),
    top_n: int = Query(10, ge=1, le=50),
):
    return get_top_hotspot_areas(days_back=days_back, top_n=top_n)


@router.get("/analysis/daily")
def get_daily_counts(
    days_back: int = Query(90, ge=1, le=365),
    nibrs_code: Optional[str] = None,
):
    return daily_counts(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/analysis/hourly")
def get_hourly_pattern(
    days_back: int = Query(90, ge=1, le=365),
    nibrs_code: Optional[str] = None,
):
    return hourly_pattern(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/analysis/day-of-week")
def get_day_of_week(
    days_back: int = Query(90, ge=1, le=365),
    nibrs_code: Optional[str] = None,
):
    return day_of_week_pattern(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/analysis/forecast")
def get_forecast(
    days_back: int = Query(90, ge=1, le=365),
    forecast_days: int = Query(14, ge=1, le=30),
    nibrs_code: Optional[str] = None,
):
    return forecast_daily(days_back=days_back, forecast_days=forecast_days, nibrs_code=nibrs_code)


@router.get("/news")
def list_news(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    geocoded_only: bool = Query(False),
):
    session = SessionLocal()
    try:
        q = session.query(NewsArticle)
        if geocoded_only:
            q = q.filter(NewsArticle.latitude.isnot(None))
        q = q.order_by(NewsArticle.published.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "total": total,
            "data": [
                {
                    "id": r.id,
                    "title": r.title,
                    "url": r.url,
                    "source": r.source,
                    "published": r.published.isoformat() if r.published else None,
                    "snippet": r.snippet,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "matched_address": r.matched_address,
                    "matched_incident_date": r.matched_incident_date.isoformat() if r.matched_incident_date else None,
                    "is_recent_crime": r.is_recent_crime,
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@router.get("/analysis/repeat-locations")
def get_repeats(
    days_back: int = Query(90, ge=1, le=1825),
    min_incidents: int = Query(3, ge=2, le=50),
    limit: int = Query(50, ge=1, le=200),
    nibrs_code: Optional[str] = None,
):
    return get_repeat_locations(days_back=days_back, min_incidents=min_incidents, limit=limit, nibrs_code=nibrs_code)


@router.get("/analysis/weather-correlation")
def get_weather_corr(days_back: int = Query(365, ge=30, le=1825)):
    return get_weather_crime_correlation(days_back=days_back)


@router.get("/311")
def list_311(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    case_type: Optional[str] = None,
    npa: Optional[str] = None,
    days_back: Optional[int] = Query(None, ge=1, le=1825),
):
    session = SessionLocal()
    try:
        q = session.query(ServiceRequest)
        if days_back:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            q = q.filter(ServiceRequest.date_created >= cutoff)
        if case_type:
            q = q.filter(ServiceRequest.case_type == case_type)
        if npa:
            q = q.filter(ServiceRequest.npa == npa)
        q = q.order_by(ServiceRequest.date_created.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "total": total,
            "data": [
                {
                    "case_number": r.case_number,
                    "case_type": r.case_type,
                    "full_address": r.full_address,
                    "description": r.description,
                    "case_status": r.case_status,
                    "date_created": r.date_created.isoformat() if r.date_created else None,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "npa": r.npa,
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@router.get("/311/stats")
def stats_311():
    session = SessionLocal()
    try:
        rows = (
            session.query(
                ServiceRequest.case_type,
                func.count().label("count"),
            )
            .group_by(ServiceRequest.case_type)
            .order_by(func.count().desc())
            .limit(20)
            .all()
        )
        return [{"case_type": r.case_type, "count": r.count} for r in rows]
    finally:
        session.close()


@router.get("/analysis/forecast-ml")
def get_forecast_ml(
    days_back: int = Query(365, ge=30, le=1825),
    forecast_days: int = Query(14, ge=1, le=30),
    nibrs_code: Optional[str] = None,
):
    return forecast_ml(days_back=days_back, forecast_days=forecast_days, nibrs_code=nibrs_code)


@router.get("/analysis/risk-map")
def get_risk_map(
    nibrs_code: Optional[str] = None,
    days_back: int = Query(90, ge=7, le=365),
):
    return compute_risk_scores(nibrs_code=nibrs_code, days_back=days_back)


@router.get("/analysis/npa-forecast/{npa}")
def get_npa_forecast(npa: str):
    return explain_npa_risk(npa)


@router.get("/analysis/npa-forecasts")
def get_npa_forecasts(
    days_back: int = Query(365, ge=30, le=1825),
    forecast_days: int = Query(7, ge=1, le=14),
    top_n: int = Query(20, ge=5, le=40),
):
    return forecast_by_npa(days_back=days_back, forecast_days=forecast_days, top_n=top_n)


@router.get("/analysis/anomalies")
def get_anomalies(
    days_back: int = Query(7, ge=1, le=90),
    threshold: float = Query(2.0, ge=1.0, le=5.0),
    nibrs_code: Optional[str] = None,
):
    return detect_anomalies(days_back=days_back, threshold_sigma=threshold, nibrs_code=nibrs_code)


@router.get("/analysis/anomaly-summary")
def get_anomaly_summary(
    days_back: int = Query(7, ge=1, le=90),
    threshold: float = Query(2.0, ge=1.0, le=5.0),
):
    return get_npa_anomaly_summary(days_back=days_back, threshold_sigma=threshold)


@router.get("/analysis/clusters")
def get_clusters(
    days_back: int = Query(30, ge=1, le=365),
    eps_km: float = Query(0.3, ge=0.1, le=2.0),
    min_samples: int = Query(5, ge=2, le=50),
    nibrs_code: Optional[str] = None,
):
    return find_clusters(days_back=days_back, eps_km=eps_km, min_samples=min_samples, nibrs_code=nibrs_code)


@router.get("/analysis/emerging-clusters")
def get_emerging_clusters(
    recent_days: int = Query(14, ge=1, le=60),
    baseline_days: int = Query(90, ge=30, le=365),
    nibrs_code: Optional[str] = None,
):
    return find_emerging_clusters(recent_days=recent_days, baseline_days=baseline_days, nibrs_code=nibrs_code)


@router.get("/analysis/proximity")
def get_proximity(
    days_back: int = Query(90, ge=1, le=365),
    radius_km: float = Query(0.5, ge=0.1, le=2.0),
    nibrs_code: Optional[str] = None,
):
    return proximity_analysis(days_back=days_back, radius_km=radius_km, nibrs_code=nibrs_code)


@router.get("/analysis/clearance")
def get_clearance(
    days_back: int = Query(365, ge=30, le=1825),
    nibrs_code: Optional[str] = None,
):
    return clearance_rates(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/analysis/clearance-time")
def get_clearance_time(days_back: int = Query(365, ge=30, le=1825)):
    return clearance_time_analysis(days_back=days_back)


@router.get("/analysis/what-changed")
def get_what_changed(
    days_back: int = Query(30, ge=7, le=1825),
    nibrs_code: Optional[str] = None,
    compare: str = Query("period"),
    compare_n: int = Query(1, ge=1, le=5),
):
    return what_changed(days_back=days_back, nibrs_code=nibrs_code, compare=compare, compare_n=compare_n)


@router.get("/analysis/type-forecast")
def get_type_forecast(
    days_back: int = Query(730, ge=90, le=1825),
    forecast_days: int = Query(7, ge=1, le=14),
    nibrs_code: Optional[str] = None,
):
    data = forecast_by_type(days_back=days_back, forecast_days=forecast_days)
    if nibrs_code:
        data = [d for d in data if d.get("nibrs_code") == nibrs_code]
    return data


@router.get("/analysis/type-forecast/{nibrs_code}/npas")
def get_type_forecast_npas(
    nibrs_code: str,
    days_back: int = Query(365, ge=30, le=1825),
    top_n: int = Query(10, ge=5, le=30),
):
    return type_forecast_by_npa(nibrs_code, days_back=days_back, top_n=top_n)


@router.get("/analysis/transitions")
def get_transitions(
    days_back: int = Query(365, ge=30, le=1825),
    window_days: int = Query(30, ge=7, le=90),
):
    return compute_transitions(days_back=days_back, window_days=window_days)


@router.get("/analysis/escalation-risk")
def get_escalation_risk(
    days_back: int = Query(180, ge=30, le=730),
    window_days: int = Query(30, ge=7, le=90),
    min_property: int = Query(2, ge=2, le=10),
    nibrs_code: Optional[str] = None,
):
    result = find_escalation_locations(days_back=days_back, window_days=window_days, min_property=min_property)
    if nibrs_code and isinstance(result, dict):
        result["at_risk_locations"] = [
            loc for loc in result.get("at_risk_locations", [])
            if nibrs_code in (loc.get("property_types") or []) or nibrs_code in (loc.get("violent_types") or [])
        ]
        result["summary"]["total_at_risk"] = len(result["at_risk_locations"])
        result["summary"]["high_risk_count"] = sum(1 for l in result["at_risk_locations"] if l.get("risk_level") == "high")
    return result


@router.get("/analysis/event-impact")
def get_event_impact_route(
    days_back: int = Query(730, ge=30, le=1825),
):
    return get_event_impact(days_back=days_back)


@router.get("/analysis/upcoming-events")
def get_upcoming_events_route(
    days_ahead: int = Query(14, ge=1, le=30),
):
    return get_upcoming_events(days_ahead=days_ahead)


@router.get("/analysis/patterns")
def get_patterns(
    days_back: int = Query(180, ge=30, le=730),
    nibrs_code: Optional[str] = None,
):
    return get_all_patterns(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/npa-metadata")
def get_npa_metadata():
    from app.analysis.risk_score import _get_npa_centers
    from app.analysis.npa_labels import get_npa_labels, label_for
    centers = _get_npa_centers()
    labels = get_npa_labels()
    names = {code: label_for(code, labels) for code in labels.keys()}
    return {
        "names": names,
        "labels": labels,
        "centers": [
            {
                "npa": npa,
                "latitude": lat,
                "longitude": lon,
                "name": names.get(str(npa)),
                "division": (labels.get(str(npa)) or {}).get("division"),
            }
            for npa, (lat, lon) in centers.items()
        ],
    }




@router.get("/analysis/per-capita")
def get_per_capita(
    days_back: int = Query(90, ge=1, le=1825),
    per: int = Query(1000, ge=100, le=100000),
):
    return per_capita_rates(days_back=days_back, per=per)


@router.get("/analysis/venues")
def get_venues(
    days_back: int = Query(365, ge=30, le=1825),
    nibrs_code: Optional[str] = None,
):
    return venue_crime_analysis(days_back=days_back, nibrs_code=nibrs_code)


@router.get("/briefing")
def get_briefing():
    return generate_briefing()


@router.get("/social")
def list_social(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    platform: Optional[str] = None,
    geocoded_only: bool = Query(False),
):
    init_social_db()
    session = SessionLocal()
    try:
        q = session.query(SocialPost)
        if platform:
            q = q.filter(SocialPost.platform == platform)
        if geocoded_only:
            q = q.filter(SocialPost.latitude.isnot(None))
        q = q.order_by(SocialPost.published.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "total": total,
            "data": [
                {
                    "id": r.id,
                    "platform": r.platform,
                    "author": r.author,
                    "content": r.content[:300] if r.content else None,
                    "url": r.url,
                    "published": r.published.isoformat() if r.published else None,
                    "subreddit": r.subreddit,
                    "score": r.score,
                    "num_comments": r.num_comments,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "matched_address": r.matched_address,
                    "matched_incident_date": r.matched_incident_date.isoformat() if r.matched_incident_date else None,
                    "is_recent_crime": r.is_recent_crime,
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@router.post("/ingest/reddit")
def trigger_reddit_ingest():
    result = ingest_reddit()
    return result


@router.post("/ingest/bluesky")
def trigger_bluesky_ingest():
    result = ingest_bluesky()
    return result


@router.post("/ingest")
def trigger_ingest(limit: int = Query(5000, ge=1, le=50000)):
    result = ingest(limit=limit)
    return result


@router.post("/ingest/news")
def trigger_news_ingest():
    result = ingest_news()
    return result


@router.post("/ingest/weather")
def trigger_weather_ingest(years_back: int = Query(5, ge=1, le=10)):
    result = ingest_weather(years_back=years_back)
    return result


@router.post("/ingest/311")
def trigger_311_ingest(years_back: int = Query(5, ge=1, le=10)):
    result = ingest_311(years_back=years_back)
    return result
