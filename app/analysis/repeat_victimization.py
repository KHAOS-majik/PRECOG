from sqlalchemy import func, case
from datetime import datetime, timedelta
from app.models.incident import Incident, SessionLocal


def get_repeat_locations(days_back=90, min_incidents=3, limit=50, nibrs_code=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(
            Incident.address,
            Incident.latitude,
            Incident.longitude,
            Incident.npa,
            Incident.division_id,
            func.count().label("incident_count"),
            func.count(func.distinct(Incident.nibrs_code)).label("crime_type_count"),
            func.min(Incident.date_reported).label("first_incident"),
            func.max(Incident.date_reported).label("last_incident"),
        ).filter(
            Incident.date_reported >= cutoff,
            Incident.address.isnot(None),
            Incident.latitude.isnot(None),
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        rows = (
            q.group_by(Incident.address)
            .having(func.count() >= min_incidents)
            .order_by(func.count().desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "address": r.address,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "npa": r.npa,
                "division_id": r.division_id,
                "incident_count": r.incident_count,
                "crime_type_count": r.crime_type_count,
                "first_incident": r.first_incident.isoformat() if r.first_incident else None,
                "last_incident": r.last_incident.isoformat() if r.last_incident else None,
                "days_span": (r.last_incident - r.first_incident).days if r.last_incident and r.first_incident else 0,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_repeat_location_details(address, days_back=365):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(Incident)
            .filter(
                Incident.address == address,
                Incident.date_reported >= cutoff,
            )
            .order_by(Incident.date_reported.desc())
            .all()
        )
        return [
            {
                "incident_report_id": r.incident_report_id,
                "date_reported": r.date_reported.isoformat() if r.date_reported else None,
                "nibrs_code": r.nibrs_code,
                "nibrs_offense_description": r.nibrs_offense_description,
                "clearance_status": r.clearance_status,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_weather_crime_correlation(days_back=365):
    from app.models.weather import DailyWeather
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(
                func.date(Incident.date_reported).label("day"),
                func.count().label("crime_count"),
                DailyWeather.temp_mean,
                DailyWeather.precipitation,
                DailyWeather.daylight_hours,
            )
            .join(DailyWeather, func.date(Incident.date_reported) == DailyWeather.date)
            .filter(Incident.date_reported >= cutoff)
            .group_by(func.date(Incident.date_reported))
            .order_by("day")
            .all()
        )

        if len(rows) < 30:
            return {"correlation": None, "message": "insufficient data"}

        import numpy as np
        counts = np.array([r.crime_count for r in rows], dtype=float)
        temps = np.array([r.temp_mean for r in rows if r.temp_mean is not None], dtype=float)

        if len(temps) == len(counts) and len(temps) > 30:
            temp_corr = float(np.corrcoef(temps, counts[:len(temps)])[0, 1])
        else:
            temp_corr = None

        temp_brackets = {}
        for r in rows:
            if r.temp_mean is None:
                continue
            if r.temp_mean < 40:
                bracket = "cold (<40F)"
            elif r.temp_mean < 60:
                bracket = "cool (40-60F)"
            elif r.temp_mean < 80:
                bracket = "warm (60-80F)"
            else:
                bracket = "hot (80F+)"
            if bracket not in temp_brackets:
                temp_brackets[bracket] = []
            temp_brackets[bracket].append(r.crime_count)

        bracket_avgs = {k: round(sum(v)/len(v), 1) for k, v in temp_brackets.items()}

        rain_days = [r.crime_count for r in rows if r.precipitation and r.precipitation > 0.1]
        dry_days = [r.crime_count for r in rows if r.precipitation is not None and r.precipitation <= 0.1]
        rain_avg = round(sum(rain_days)/len(rain_days), 1) if rain_days else None
        dry_avg = round(sum(dry_days)/len(dry_days), 1) if dry_days else None

        return {
            "temp_correlation": round(temp_corr, 3) if temp_corr else None,
            "avg_by_temp": bracket_avgs,
            "rain_day_avg": rain_avg,
            "dry_day_avg": dry_avg,
            "data_points": len(rows),
        }
    finally:
        session.close()
