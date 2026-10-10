import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal
from app.models.weather import DailyWeather
from app.models.service_request import ServiceRequest

US_HOLIDAYS = {
    (1, 1), (1, 15), (2, 19), (5, 27), (6, 19),
    (7, 4), (9, 1), (10, 13), (11, 11), (11, 27), (12, 25),
}

FEATURE_NAMES = [
    "day_of_week", "month", "day_of_month", "is_weekend", "is_holiday",
    "temp_mean", "temp_max", "precipitation", "wind_speed", "daylight_hours",
    "lag_7", "lag_14", "lag_28", "rolling_7", "rolling_14",
]


def _is_holiday(d):
    return 1 if (d.month, d.day) in US_HOLIDAYS else 0


def build_daily_features(days_back=365, nibrs_code=None, npa=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        q = session.query(
            func.date(Incident.date_reported).label("day"),
            func.count().label("count"),
        ).filter(Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        if npa:
            q = q.filter(Incident.npa == npa)
        crime_rows = q.group_by(func.date(Incident.date_reported)).order_by("day").all()

        weather_rows = session.query(DailyWeather).filter(DailyWeather.date >= cutoff.date()).all()
        weather_map = {w.date: w for w in weather_rows}

        records = []
        counts = [r.count for r in crime_rows]
        dates = []
        for r in crime_rows:
            d = datetime.strptime(str(r.day), "%Y-%m-%d").date() if isinstance(r.day, str) else r.day
            dates.append(d)

        for i, (d, count) in enumerate(zip(dates, counts)):
            w = weather_map.get(d)
            lag_7 = counts[i - 7] if i >= 7 else np.mean(counts[:max(i, 1)])
            lag_14 = counts[i - 14] if i >= 14 else np.mean(counts[:max(i, 1)])
            lag_28 = counts[i - 28] if i >= 28 else np.mean(counts[:max(i, 1)])
            window_7 = counts[max(0, i - 7):i] or counts[:1]
            window_14 = counts[max(0, i - 14):i] or counts[:1]

            records.append({
                "date": d,
                "count": count,
                "day_of_week": d.weekday(),
                "month": d.month,
                "day_of_month": d.day,
                "is_weekend": 1 if d.weekday() >= 5 else 0,
                "is_holiday": _is_holiday(d),
                "temp_mean": w.temp_mean if w and w.temp_mean is not None else None,
                "temp_max": w.temp_max if w and w.temp_max is not None else None,
                "precipitation": w.precipitation if w and w.precipitation is not None else 0,
                "wind_speed": w.wind_speed_max if w and w.wind_speed_max is not None else None,
                "daylight_hours": w.daylight_hours if w and w.daylight_hours is not None else None,
                "lag_7": lag_7,
                "lag_14": lag_14,
                "lag_28": lag_28,
                "rolling_7": np.mean(window_7),
                "rolling_14": np.mean(window_14),
            })
        return records
    finally:
        session.close()


def to_feature_vector(rec):
    return [
        rec["day_of_week"],
        rec["month"],
        rec["day_of_month"],
        rec["is_weekend"],
        rec.get("is_holiday", 0),
        rec.get("temp_mean") or 65,
        rec.get("temp_max") or 80,
        rec.get("precipitation") or 0,
        rec.get("wind_speed") or 5,
        rec.get("daylight_hours") or 12,
        rec["lag_7"],
        rec["lag_14"],
        rec["lag_28"],
        rec["rolling_7"],
        rec["rolling_14"],
    ]


def build_future_features(last_rec, recent_counts, forecast_list, day_offset):
    fd = last_rec["date"] + timedelta(days=day_offset)
    i = day_offset

    lag_7 = forecast_list[-1]["predicted_count"] if forecast_list else recent_counts[-1]
    if (14 - i) > 0:
        lag_14 = recent_counts[-14 + i]
    elif i > 14 and len(forecast_list) > i - 15:
        lag_14 = forecast_list[i - 15]["predicted_count"]
    else:
        lag_14 = lag_7
    lag_28 = recent_counts[-28 + i] if (28 - i) > 0 else lag_7

    recent_window = recent_counts[-(7 - min(i, 6)):] + [f["predicted_count"] for f in forecast_list[-min(i, 6):]]
    rolling_7 = np.mean(recent_window[-7:]) if recent_window else recent_counts[-1]
    rolling_14 = np.mean(recent_window[-14:]) if len(recent_window) >= 14 else rolling_7

    return {
        "date": fd,
        "day_of_week": fd.weekday(),
        "month": fd.month,
        "day_of_month": fd.day,
        "is_weekend": 1 if fd.weekday() >= 5 else 0,
        "is_holiday": _is_holiday(fd),
        "temp_mean": last_rec.get("temp_mean"),
        "temp_max": last_rec.get("temp_max"),
        "precipitation": 0,
        "wind_speed": last_rec.get("wind_speed"),
        "daylight_hours": last_rec.get("daylight_hours"),
        "lag_7": lag_7,
        "lag_14": lag_14,
        "lag_28": lag_28,
        "rolling_7": rolling_7,
        "rolling_14": rolling_14,
    }


def get_311_density_by_npa(days_back=90):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(ServiceRequest.npa, func.count().label("count"))
            .filter(ServiceRequest.date_created >= cutoff, ServiceRequest.npa.isnot(None))
            .group_by(ServiceRequest.npa)
            .all()
        )
        return {r.npa: r.count for r in rows}
    finally:
        session.close()
