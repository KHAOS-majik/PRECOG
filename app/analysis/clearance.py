from datetime import datetime, timedelta
from sqlalchemy import func, case
from app.models.incident import Incident, SessionLocal


def clearance_rates(days_back=365, nibrs_code=None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        q = session.query(
            Incident.nibrs_code,
            Incident.nibrs_offense_description,
            func.count().label("total"),
            func.sum(case(
                (Incident.clearance_status.isnot(None), 1), else_=0
            )).label("cleared"),
        ).filter(Incident.date_reported >= cutoff)
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        by_type = (
            q.group_by(Incident.nibrs_code, Incident.nibrs_offense_description)
            .having(func.count() >= 10)
            .order_by(func.count().desc())
            .all()
        )

        type_rates = []
        for r in by_type[:20]:
            rate = round(r.cleared / r.total * 100, 1) if r.total > 0 else 0
            type_rates.append({
                "nibrs_code": r.nibrs_code,
                "description": r.nibrs_offense_description,
                "total": r.total,
                "cleared": r.cleared,
                "clearance_rate": rate,
            })

        dq = session.query(
            Incident.division_id,
            func.count().label("total"),
            func.sum(case(
                (Incident.clearance_status.isnot(None), 1), else_=0
            )).label("cleared"),
        ).filter(Incident.date_reported >= cutoff, Incident.division_id.isnot(None))
        if nibrs_code:
            dq = dq.filter(Incident.nibrs_code == nibrs_code)
        by_division = dq.group_by(Incident.division_id).order_by(func.count().desc()).all()

        division_rates = []
        for r in by_division:
            rate = round(r.cleared / r.total * 100, 1) if r.total > 0 else 0
            division_rates.append({
                "division_id": r.division_id,
                "total": r.total,
                "cleared": r.cleared,
                "clearance_rate": rate,
            })

        overall_total = sum(r.total for r in by_type)
        overall_cleared = sum(r.cleared for r in by_type)
        overall_rate = round(overall_cleared / overall_total * 100, 1) if overall_total > 0 else 0

        status_breakdown = (
            session.query(
                Incident.clearance_status,
                func.count().label("count"),
            )
            .filter(Incident.date_reported >= cutoff)
            .group_by(Incident.clearance_status)
            .order_by(func.count().desc())
            .all()
        )

        statuses = [{"status": r.clearance_status or "Uncleared", "count": r.count} for r in status_breakdown]

        return {
            "overall_rate": overall_rate,
            "overall_total": overall_total,
            "overall_cleared": overall_cleared,
            "by_type": type_rates,
            "by_division": division_rates,
            "status_breakdown": statuses,
        }
    finally:
        session.close()


def clearance_time_analysis(days_back=365):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(
                Incident.nibrs_code,
                Incident.nibrs_offense_description,
                Incident.date_reported,
                Incident.clearance_date,
            )
            .filter(
                Incident.date_reported >= cutoff,
                Incident.clearance_date.isnot(None),
                Incident.date_reported.isnot(None),
            )
            .all()
        )

        from collections import defaultdict
        type_times = defaultdict(list)
        for r in rows:
            days_to_clear = (r.clearance_date - r.date_reported).days
            if 0 <= days_to_clear <= 365:
                type_times[r.nibrs_code].append(days_to_clear)

        import numpy as np
        result = []
        for code, times in type_times.items():
            if len(times) < 5:
                continue
            result.append({
                "nibrs_code": code,
                "median_days": round(float(np.median(times)), 1),
                "mean_days": round(float(np.mean(times)), 1),
                "cases": len(times),
                "same_day": sum(1 for t in times if t == 0),
                "within_week": sum(1 for t in times if t <= 7),
            })

        result.sort(key=lambda r: r["cases"], reverse=True)
        return result[:20]
    finally:
        session.close()
