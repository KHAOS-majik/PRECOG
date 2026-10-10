from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal


def what_changed(days_back=30, nibrs_code=None, compare="period", compare_n=1):
    session = SessionLocal()
    try:
        now = datetime.utcnow()

        if compare == "day":
            span = compare_n
            current_start = now - timedelta(days=span)
            current_end = now
            prev_start = current_start - timedelta(days=span)
            prev_end = current_start
            label_current = f"Last {compare_n} day{'s' if compare_n > 1 else ''}"
            label_previous = f"Prior {compare_n} day{'s' if compare_n > 1 else ''}"
        elif compare == "week":
            span = 7 * compare_n
            current_start = now - timedelta(days=span)
            current_end = now
            prev_start = current_start - timedelta(days=span)
            prev_end = current_start
            label_current = f"Last {compare_n} week{'s' if compare_n > 1 else ''}"
            label_previous = f"Prior {compare_n} week{'s' if compare_n > 1 else ''}"
        elif compare == "month":
            span = 30 * compare_n
            current_start = now - timedelta(days=span)
            current_end = now
            prev_start = current_start - timedelta(days=span)
            prev_end = current_start
            label_current = f"Last {compare_n} month{'s' if compare_n > 1 else ''}"
            label_previous = f"Prior {compare_n} month{'s' if compare_n > 1 else ''}"
        elif compare == "year":
            current_start = now - timedelta(days=days_back)
            current_end = now
            prev_start = current_start - timedelta(days=365 * compare_n)
            prev_end = current_end - timedelta(days=365 * compare_n)
            label_current = f"Last {days_back} days"
            label_previous = f"Same period {compare_n} year{'s' if compare_n > 1 else ''} ago"
        else:
            current_start = now - timedelta(days=days_back)
            current_end = now
            prev_start = current_start - timedelta(days=days_back)
            prev_end = current_start
            label_current = f"Last {days_back} days"
            label_previous = f"Prior {days_back} days"

        def _period_stats(start, end):
            q = session.query(Incident).filter(
                Incident.date_reported >= start,
                Incident.date_reported < end,
            )
            if nibrs_code:
                q = q.filter(Incident.nibrs_code == nibrs_code)
            total = q.count()

            tq = session.query(
                Incident.nibrs_code,
                Incident.nibrs_offense_description,
                func.count().label("count"),
            ).filter(Incident.date_reported >= start, Incident.date_reported < end)
            if nibrs_code:
                tq = tq.filter(Incident.nibrs_code == nibrs_code)
            by_type = tq.group_by(Incident.nibrs_code, Incident.nibrs_offense_description).order_by(func.count().desc()).all()

            nq = session.query(
                Incident.npa,
                func.count().label("count"),
            ).filter(
                Incident.date_reported >= start,
                Incident.date_reported < end,
                Incident.npa.isnot(None),
            )
            if nibrs_code:
                nq = nq.filter(Incident.nibrs_code == nibrs_code)
            by_npa = nq.group_by(Incident.npa).order_by(func.count().desc()).all()

            return {
                "total": total,
                "by_type": {r.nibrs_code: {"desc": r.nibrs_offense_description, "count": r.count} for r in by_type},
                "by_npa": {r.npa: r.count for r in by_npa},
            }

        current = _period_stats(current_start, current_end)
        previous = _period_stats(prev_start, prev_end)

        total_change = current["total"] - previous["total"]
        total_pct = round(total_change / max(previous["total"], 1) * 100, 1)

        type_changes = []
        all_types = set(list(current["by_type"].keys()) + list(previous["by_type"].keys()))
        for code in all_types:
            cur = current["by_type"].get(code, {"desc": "", "count": 0})
            prev = previous["by_type"].get(code, {"desc": "", "count": 0})
            delta = cur["count"] - prev["count"]
            if prev["count"] > 0:
                pct = round(delta / prev["count"] * 100, 1)
            elif cur["count"] > 0:
                pct = 100.0
            else:
                continue
            type_changes.append({
                "nibrs_code": code,
                "description": cur["desc"] or prev["desc"],
                "current": cur["count"],
                "previous": prev["count"],
                "delta": delta,
                "pct_change": pct,
            })

        type_changes.sort(key=lambda t: abs(t["delta"]), reverse=True)

        npa_changes = []
        all_npas = set(list(current["by_npa"].keys()) + list(previous["by_npa"].keys()))
        for npa in all_npas:
            cur = current["by_npa"].get(npa, 0)
            prev = previous["by_npa"].get(npa, 0)
            delta = cur - prev
            pct = round(delta / max(prev, 1) * 100, 1)
            npa_changes.append({
                "npa": npa,
                "current": cur,
                "previous": prev,
                "delta": delta,
                "pct_change": pct,
            })
        npa_changes.sort(key=lambda n: abs(n["delta"]), reverse=True)

        rising = [t for t in type_changes if t["delta"] > 0][:10]
        falling = [t for t in type_changes if t["delta"] < 0][:10]
        hottest_npas = [n for n in npa_changes if n["delta"] > 0][:10]
        cooling_npas = [n for n in npa_changes if n["delta"] < 0][:10]

        return {
            "compare": compare,
            "compare_n": compare_n,
            "period_days": days_back,
            "label_current": label_current,
            "label_previous": label_previous,
            "current_start": current_start.strftime("%Y-%m-%d"),
            "current_end": current_end.strftime("%Y-%m-%d"),
            "previous_start": prev_start.strftime("%Y-%m-%d"),
            "previous_end": prev_end.strftime("%Y-%m-%d"),
            "current_total": current["total"],
            "previous_total": previous["total"],
            "total_change": total_change,
            "total_pct_change": total_pct,
            "rising_types": rising,
            "falling_types": falling,
            "hottest_npas": hottest_npas,
            "cooling_npas": cooling_npas,
        }
    finally:
        session.close()
