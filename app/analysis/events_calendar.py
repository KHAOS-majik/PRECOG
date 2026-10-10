from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy import func
import numpy as np
from app.models.incident import Incident, SessionLocal
from app.analysis.events import CHARLOTTE_VENUES

MAJOR_HOLIDAYS = {
    "New Year's Day": (1, 1),
    "MLK Day": (1, 15),
    "Memorial Day": (5, 27),
    "Juneteenth": (6, 19),
    "Independence Day": (7, 4),
    "Labor Day": (9, 1),
    "Halloween": (10, 31),
    "Thanksgiving": (11, 27),
    "Christmas Eve": (12, 24),
    "Christmas Day": (12, 25),
    "New Year's Eve": (12, 31),
}

CHARLOTTE_ANNUAL_EVENTS = [
    {"name": "CIAA Tournament", "month": 2, "day": 25, "duration": 5, "venue": "Spectrum Center", "type": "tournament"},
    {"name": "Speed Street / 600 Festival", "month": 5, "day": 24, "duration": 3, "venue": "Uptown", "type": "festival"},
    {"name": "Charlotte Pride", "month": 8, "day": 17, "duration": 2, "venue": "Uptown", "type": "festival"},
    {"name": "Bojangles Southern 500", "month": 9, "day": 1, "duration": 2, "venue": "Charlotte Motor Speedway", "type": "race"},
    {"name": "Charlotte Oktoberfest", "month": 10, "day": 5, "duration": 2, "venue": "NC Music Factory", "type": "festival"},
    {"name": "Bank of America ROVAL 400", "month": 10, "day": 12, "duration": 3, "venue": "Charlotte Motor Speedway", "type": "race"},
]

SCHOOL_BREAKS = [
    {"name": "Winter Break", "start_month": 12, "start_day": 20, "end_month": 1, "end_day": 3},
    {"name": "Spring Break", "start_month": 3, "start_day": 28, "end_month": 4, "end_day": 5},
    {"name": "Summer Break", "start_month": 6, "start_day": 10, "end_month": 8, "end_day": 25},
]

GAME_SCHEDULES = {
    "Panthers": {"venue": "Bank of America Stadium", "months": [9, 10, 11, 12, 1], "games_per_month": 2, "day_of_week": 6},
    "Hornets": {"venue": "Spectrum Center", "months": [10, 11, 12, 1, 2, 3, 4], "games_per_month": 5, "day_of_week": None},
    "Charlotte FC": {"venue": "Bank of America Stadium", "months": [3, 4, 5, 6, 7, 8, 9, 10], "games_per_month": 2, "day_of_week": 5},
    "Knights": {"venue": "Truist Field", "months": [4, 5, 6, 7, 8, 9], "games_per_month": 8, "day_of_week": None},
}


def _is_school_break(d):
    for brk in SCHOOL_BREAKS:
        start = date(d.year, brk["start_month"], brk["start_day"])
        end_year = d.year if brk["end_month"] >= brk["start_month"] else d.year + 1
        end = date(end_year, brk["end_month"], brk["end_day"])
        if start <= d <= end:
            return brk["name"]
    return None


def _nearby_events(target_date, window_days=7):
    events = []
    for name, (m, d) in MAJOR_HOLIDAYS.items():
        try:
            h = date(target_date.year, m, d)
        except ValueError:
            continue
        gap = (h - target_date).days
        if -1 <= gap <= window_days:
            events.append({"name": name, "date": str(h), "type": "holiday", "days_away": gap})

    for evt in CHARLOTTE_ANNUAL_EVENTS:
        try:
            e = date(target_date.year, evt["month"], evt["day"])
        except ValueError:
            continue
        gap = (e - target_date).days
        if -evt["duration"] <= gap <= window_days:
            events.append({"name": evt["name"], "date": str(e), "type": evt["type"], "venue": evt.get("venue"), "days_away": gap})

    for team, sched in GAME_SCHEDULES.items():
        if target_date.month in sched["months"]:
            events.append({"name": f"{team} game (est.)", "type": "game", "venue": sched["venue"], "days_away": 0})

    brk = _is_school_break(target_date)
    if brk:
        events.append({"name": brk, "type": "school_break", "days_away": 0})

    return events


def get_event_impact(days_back=730):
    """Analyze historical crime impact around events and venues."""
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        results = []

        for name, (m, d) in MAJOR_HOLIDAYS.items():
            holiday_dates = []
            for year in range(cutoff.year, datetime.utcnow().year + 1):
                try:
                    hd = date(year, m, d)
                except ValueError:
                    continue
                if hd >= cutoff.date():
                    holiday_dates.append(hd)

            if not holiday_dates:
                continue

            event_counts = []
            baseline_counts = []
            for hd in holiday_dates:
                event_start = datetime(hd.year, hd.month, hd.day) - timedelta(hours=6)
                event_end = event_start + timedelta(days=2)
                ec = session.query(func.count()).filter(
                    Incident.date_reported >= event_start,
                    Incident.date_reported < event_end,
                ).scalar()
                event_counts.append(ec)

                base_start = event_start - timedelta(days=14)
                base_end = base_start + timedelta(days=2)
                bc = session.query(func.count()).filter(
                    Incident.date_reported >= base_start,
                    Incident.date_reported < base_end,
                ).scalar()
                baseline_counts.append(bc)

            avg_event = np.mean(event_counts) if event_counts else 0
            avg_base = np.mean(baseline_counts) if baseline_counts else 1
            impact_pct = round((avg_event - avg_base) / max(avg_base, 1) * 100, 1)

            top_types_q = session.query(
                Incident.nibrs_code, Incident.nibrs_offense_description,
                func.count().label("c"),
            )
            for hd in holiday_dates:
                top_types_q = top_types_q.filter(
                    Incident.date_reported >= datetime(hd.year, hd.month, hd.day) - timedelta(hours=6),
                    Incident.date_reported < datetime(hd.year, hd.month, hd.day) + timedelta(days=2),
                )
            top_types = []

            hour_q = session.query(
                func.strftime('%H', Incident.date_reported).label('hr'),
                func.count().label("c"),
            )
            for hd in holiday_dates[-1:]:
                hr_rows = hour_q.filter(
                    Incident.date_reported >= datetime(hd.year, hd.month, hd.day) - timedelta(hours=6),
                    Incident.date_reported < datetime(hd.year, hd.month, hd.day) + timedelta(days=2),
                ).group_by('hr').order_by(func.count().desc()).limit(3).all()
                peak_hours = [int(r.hr) for r in hr_rows]

            results.append({
                "event": name,
                "type": "holiday",
                "avg_incidents": round(avg_event, 0),
                "avg_baseline": round(avg_base, 0),
                "impact_pct": impact_pct,
                "occurrences": len(holiday_dates),
                "peak_hours": peak_hours if 'peak_hours' in dir() else [],
            })

        results.sort(key=lambda r: abs(r["impact_pct"]), reverse=True)
        return results
    finally:
        session.close()


def get_upcoming_events(days_ahead=14):
    """Get events coming up in the next N days with historical impact data."""
    today = date.today()
    upcoming = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        events = _nearby_events(d, window_days=0)
        for evt in events:
            evt["event_date"] = str(d)
            evt["days_from_now"] = i
            upcoming.append(evt)

    seen = set()
    deduped = []
    for evt in upcoming:
        key = evt["name"] + evt.get("event_date", "")
        if key not in seen:
            seen.add(key)
            deduped.append(evt)

    return deduped


def get_event_features(target_date):
    """Return event features for a given date (for ML model input)."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    events = _nearby_events(target_date, window_days=0)
    is_game_day = any(e["type"] == "game" for e in events)
    is_holiday = any(e["type"] == "holiday" for e in events)
    is_school_break = _is_school_break(target_date) is not None
    is_event = any(e["type"] in ("festival", "tournament", "race") for e in events)

    return {
        "is_game_day": 1 if is_game_day else 0,
        "is_holiday_event": 1 if is_holiday else 0,
        "is_school_break": 1 if is_school_break else 0,
        "is_major_event": 1 if is_event else 0,
        "event_count": len(events),
    }
