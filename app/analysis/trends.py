import numpy as np
from sqlalchemy import func, extract
from datetime import datetime, timedelta
from app.models.incident import Incident, SessionLocal


def daily_counts(days_back: int = 90, nibrs_code: str = None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            func.date(Incident.date_reported).label("day"),
            func.count().label("count"),
        ).filter(Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by(func.date(Incident.date_reported)).order_by("day").all()
        return [{"date": str(r.day), "count": r.count} for r in rows]
    finally:
        session.close()


def hourly_pattern(days_back: int = 90, nibrs_code: str = None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            extract("hour", Incident.date_reported).label("hour"),
            func.count().label("count"),
        ).filter(
            Incident.date_reported >= cutoff,
            Incident.date_reported.isnot(None),
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by("hour").order_by("hour").all()
        hours = {int(r.hour): r.count for r in rows}
        return [{"hour": h, "count": hours.get(h, 0)} for h in range(24)]
    finally:
        session.close()


def day_of_week_pattern(days_back: int = 90, nibrs_code: str = None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            extract("dow", Incident.date_reported).label("dow"),
            func.count().label("count"),
        ).filter(
            Incident.date_reported >= cutoff,
            Incident.date_reported.isnot(None),
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = q.group_by("dow").order_by("dow").all()
        dow_map = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
        dow_data = {int(r.dow): r.count for r in rows}
        return [{"day": dow_map.get(d, str(d)), "dow": d, "count": dow_data.get(d, 0)} for d in range(7)]
    finally:
        session.close()


def forecast_daily(days_back: int = 90, forecast_days: int = 14, nibrs_code: str = None):
    history = daily_counts(days_back, nibrs_code)
    if len(history) < 14:
        return {"history": history, "forecast": [], "trend": "insufficient_data"}

    counts = np.array([d["count"] for d in history], dtype=np.float64)
    x = np.arange(len(counts), dtype=np.float64)
    coeffs = np.polyfit(x, counts, 1)
    slope, intercept = coeffs

    window = min(7, len(counts))
    weekly_avg = np.convolve(counts, np.ones(window) / window, mode="valid")

    if slope > 0.5:
        trend = "increasing"
    elif slope < -0.5:
        trend = "decreasing"
    else:
        trend = "stable"

    last_date = datetime.strptime(history[-1]["date"], "%Y-%m-%d")
    forecast = []
    for i in range(1, forecast_days + 1):
        future_x = len(counts) - 1 + i
        predicted = max(0, slope * future_x + intercept)
        day_of_week = (last_date + timedelta(days=i)).weekday()
        dow_counts = counts[np.arange(len(counts)) % 7 == day_of_week]
        if len(dow_counts) > 0:
            dow_factor = dow_counts.mean() / max(counts.mean(), 1)
            predicted *= dow_factor
        forecast_date = (last_date + timedelta(days=i)).strftime("%Y-%m-%d")
        forecast.append({"date": forecast_date, "predicted_count": round(predicted, 1)})

    return {
        "history": history,
        "forecast": forecast,
        "trend": trend,
        "avg_daily": round(float(counts.mean()), 1),
        "slope": round(float(slope), 3),
    }
