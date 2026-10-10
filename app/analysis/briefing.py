from datetime import datetime, timedelta
from app.analysis.anomaly import detect_anomalies
from app.analysis.clusters import find_emerging_clusters
from app.analysis.forecaster import forecast_ml
from app.analysis.clearance import clearance_rates
from app.analysis.comparison import what_changed
from app.analysis.risk_score import compute_risk_scores, _risk_level
from app.analysis.events_calendar import get_upcoming_events, get_event_impact
from app.analysis.type_forecaster import forecast_by_type
from app.analysis.type_transition import find_escalation_locations
from app.analysis.patterns import detect_temporal_patterns, detect_geographic_drift
from app.models.weather import DailyWeather
from app.models.incident import SessionLocal
from sqlalchemy import func


def generate_briefing():
    now = datetime.utcnow()

    forecast = forecast_ml(days_back=365, forecast_days=7)
    anomalies = detect_anomalies(days_back=7, lookback_baseline=365, threshold_sigma=2.0)
    emerging = find_emerging_clusters(recent_days=14, baseline_days=90)
    changes = what_changed(days_back=7)
    clearance = clearance_rates(days_back=90)

    try:
        risk_scores = compute_risk_scores()
    except Exception:
        risk_scores = []

    try:
        upcoming = get_upcoming_events(days_ahead=7)
    except Exception:
        upcoming = []

    try:
        type_forecasts = forecast_by_type(days_back=730, forecast_days=7)
    except Exception:
        type_forecasts = []

    try:
        escalation = find_escalation_locations(days_back=180)
    except Exception:
        escalation = {"summary": {"total_at_risk": 0, "high_risk_count": 0}, "at_risk_locations": []}

    try:
        temporal_patterns = detect_temporal_patterns(days_back=90, min_occurrences=4)
    except Exception:
        temporal_patterns = []

    try:
        drift = detect_geographic_drift(days_back=180)
    except Exception:
        drift = []

    session = SessionLocal()
    try:
        latest_weather = (
            session.query(DailyWeather)
            .order_by(DailyWeather.date.desc())
            .first()
        )
        weather_info = None
        if latest_weather:
            weather_info = {
                "date": str(latest_weather.date),
                "temp_high": latest_weather.temp_max,
                "temp_low": latest_weather.temp_min,
                "precipitation": latest_weather.precipitation,
                "wind": latest_weather.wind_speed_max,
            }
    finally:
        session.close()

    # === CRITICAL ALERTS (0-5) ===
    critical_alerts = []

    critical_anomalies = [a for a in anomalies if a["severity"] == "critical"]
    if critical_anomalies:
        critical_alerts.append({
            "level": "critical",
            "type": "anomaly_spike",
            "message": f"{len(critical_anomalies)} neighborhoods with critical crime spikes",
            "details": [f"NPA {a['npa']}: {a['count']} incidents ({a['z_score']}σ above normal)" for a in critical_anomalies[:5]],
        })

    critical_npas = [r for r in risk_scores if r["risk_score"] >= 75]
    if critical_npas:
        critical_alerts.append({
            "level": "critical",
            "type": "high_risk_npas",
            "message": f"{len(critical_npas)} NPAs at CRITICAL risk level (75+)",
            "details": [f"NPA {r['npa']}: {r['risk_score']}/100 (top factor: {r['top_factor']})" for r in critical_npas[:5]],
        })

    if escalation["summary"]["high_risk_count"] > 10:
        critical_alerts.append({
            "level": "warning",
            "type": "escalation_risk",
            "message": f"{escalation['summary']['high_risk_count']} locations at high risk of property→violent escalation",
            "details": [f"{loc['address']}: {loc['property_count']} property crimes" for loc in escalation["at_risk_locations"][:3]],
        })

    if emerging:
        new_clusters = [e for e in emerging if e.get("is_new")]
        if new_clusters:
            critical_alerts.append({
                "level": "warning",
                "type": "emerging_cluster",
                "message": f"{len(new_clusters)} new crime clusters detected in 14 days",
                "details": [f"Cluster at ({c['center_lat']:.4f}, {c['center_lon']:.4f}): {c['incident_count']} incidents" for c in new_clusters[:3]],
            })

    if changes["total_pct_change"] > 10:
        critical_alerts.append({
            "level": "warning",
            "type": "volume_increase",
            "message": f"Crime volume up {changes['total_pct_change']}% vs prior week",
        })

    if weather_info and weather_info.get("temp_high") and weather_info["temp_high"] > 90:
        critical_alerts.append({
            "level": "info",
            "type": "weather_risk",
            "message": f"High temp {weather_info['temp_high']}°F — historically correlated with elevated activity",
        })

    # === PREDICTIVE OUTLOOK (7 days) ===
    predicted_next_7 = []
    if forecast.get("forecast"):
        for f in forecast["forecast"][:7]:
            predicted_next_7.append({"date": f["date"], "predicted": f["predicted_count"]})

    high_risk_npas = [{"npa": r["npa"], "risk_score": r["risk_score"], "risk_level": _risk_level(r["risk_score"]), "top_factor": r["top_factor"]} for r in risk_scores[:10]]

    rising_types = [{"code": t["nibrs_code"], "description": t["description"], "pct_change": t["pct_change"], "forecast_avg": t["forecast_avg"]} for t in type_forecasts if t["trend"] == "increasing"]
    falling_types = [{"code": t["nibrs_code"], "description": t["description"], "pct_change": t["pct_change"], "forecast_avg": t["forecast_avg"]} for t in type_forecasts if t["trend"] == "decreasing"]

    # === EVENT AWARENESS ===
    event_section = {
        "upcoming_events": upcoming[:10],
        "event_count": len(upcoming),
    }

    # === PATTERNS & TRENDS ===
    pattern_section = {
        "temporal_patterns": temporal_patterns[:5],
        "geographic_drift": drift[:5],
        "temporal_count": len(temporal_patterns),
        "drift_count": len(drift),
    }

    # === MODEL CONFIDENCE ===
    model_confidence = {
        "forecast_r2": forecast.get("test_r2") or forecast.get("r2_score"),
        "forecast_model": forecast.get("model"),
        "from_cache": forecast.get("from_cache", False),
        "data_freshness": forecast.get("trained_at"),
    }
    r2 = model_confidence["forecast_r2"]
    if r2 is not None:
        if r2 >= 0.5:
            model_confidence["status"] = "good"
        elif r2 >= 0.2:
            model_confidence["status"] = "fair"
        else:
            model_confidence["status"] = "low"

    return {
        "generated_at": now.isoformat(),
        "critical_alerts": critical_alerts,
        "predictive_outlook": {
            "trend": forecast.get("trend"),
            "avg_daily": forecast.get("avg_daily"),
            "next_7_days": predicted_next_7,
            "high_risk_npas": high_risk_npas,
            "rising_types": rising_types,
            "falling_types": falling_types,
        },
        "area_intelligence": {
            "anomaly_count": len(anomalies),
            "top_anomalies": anomalies[:5],
            "emerging_clusters": emerging[:5],
            "escalation_risk": {
                "at_risk_count": escalation["summary"]["total_at_risk"],
                "high_risk_count": escalation["summary"]["high_risk_count"],
                "top_locations": escalation["at_risk_locations"][:5],
            },
        },
        "patterns_and_trends": pattern_section,
        "events": event_section,
        "period_comparison": {
            "current_total": changes["current_total"],
            "previous_total": changes["previous_total"],
            "pct_change": changes["total_pct_change"],
            "rising_types": changes["rising_types"][:5],
            "falling_types": changes["falling_types"][:5],
        },
        "model_confidence": model_confidence,
        "clearance_rate": clearance.get("overall_rate"),
        "weather": weather_info,
    }
