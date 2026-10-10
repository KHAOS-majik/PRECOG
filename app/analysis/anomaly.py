import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal


def detect_anomalies(days_back=90, lookback_baseline=365, threshold_sigma=2.0, nibrs_code=None):
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        baseline_cutoff = now - timedelta(days=lookback_baseline)
        recent_cutoff = now - timedelta(days=days_back)

        bq = session.query(
            Incident.npa,
            func.date(Incident.date_reported).label("day"),
            func.count().label("count"),
        ).filter(Incident.date_reported >= baseline_cutoff, Incident.npa.isnot(None))
        if nibrs_code:
            bq = bq.filter(Incident.nibrs_code == nibrs_code)
        baseline_rows = bq.group_by(Incident.npa, func.date(Incident.date_reported)).all()

        from collections import defaultdict
        npa_baseline = defaultdict(list)
        for r in baseline_rows:
            npa_baseline[r.npa].append(r.count)

        rq = session.query(
            Incident.npa,
            func.date(Incident.date_reported).label("day"),
            func.count().label("count"),
        ).filter(Incident.date_reported >= recent_cutoff, Incident.npa.isnot(None))
        if nibrs_code:
            rq = rq.filter(Incident.nibrs_code == nibrs_code)
        recent_rows = rq.group_by(Incident.npa, func.date(Incident.date_reported)).order_by(func.date(Incident.date_reported).desc()).all()

        npa_recent = defaultdict(list)
        for r in recent_rows:
            npa_recent[r.npa].append({"day": str(r.day), "count": r.count})

        anomalies = []
        for npa, daily_counts in npa_baseline.items():
            if len(daily_counts) < 14:
                continue
            mean = np.mean(daily_counts)
            std = np.std(daily_counts)
            if std < 1:
                continue

            recent = npa_recent.get(npa, [])
            for day_data in recent[:7]:
                z_score = (day_data["count"] - mean) / std
                if abs(z_score) >= threshold_sigma:
                    anomalies.append({
                        "npa": npa,
                        "date": day_data["day"],
                        "count": day_data["count"],
                        "baseline_mean": round(mean, 1),
                        "baseline_std": round(std, 1),
                        "z_score": round(z_score, 2),
                        "direction": "spike" if z_score > 0 else "drop",
                        "severity": "critical" if abs(z_score) >= 3 else "warning",
                    })

        anomalies.sort(key=lambda a: abs(a["z_score"]), reverse=True)
        return anomalies[:50]
    finally:
        session.close()


def get_npa_anomaly_summary(days_back=7, lookback_baseline=365, threshold_sigma=2.0):
    anomalies = detect_anomalies(days_back, lookback_baseline, threshold_sigma)
    from collections import Counter
    npa_counts = Counter()
    npa_worst = {}
    for a in anomalies:
        npa_counts[a["npa"]] += 1
        if a["npa"] not in npa_worst or abs(a["z_score"]) > abs(npa_worst[a["npa"]]["z_score"]):
            npa_worst[a["npa"]] = a

    summary = []
    for npa, count in npa_counts.most_common(20):
        worst = npa_worst[npa]
        summary.append({
            "npa": npa,
            "anomaly_days": count,
            "worst_z_score": worst["z_score"],
            "worst_date": worst["date"],
            "worst_count": worst["count"],
            "baseline_mean": worst["baseline_mean"],
            "severity": worst["severity"],
        })
    return summary
