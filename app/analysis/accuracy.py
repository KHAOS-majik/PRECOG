"""Score Precog's weekly predictions against what CMPD later reported.

Each week (Monday to Sunday) gets one row. Predictions are saved before the
week begins when the app is running ("live"), or rebuilt from data dated
before the week when it was not ("reconstructed"). A week is scored once CMPD
has had time to publish it.
"""
import json
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from app.clock import as_of, data_through, reset_data_through
from app.models.accuracy import AccuracyWeek, init_accuracy_db
from app.models.incident import Incident, SessionLocal

log = logging.getLogger("precog.accuracy")

TOP_N = 10
# CMPD keeps adding reports for a couple of days; scoring earlier would undercount.
PUBLICATION_LAG_DAYS = 2
BACKFILL_WEEKS = 12


def _dt(d):
    return datetime.combine(d, time.min)


def _today():
    return (datetime.utcnow() - timedelta(hours=5)).date()


def _monday_on_or_before(d):
    return d - timedelta(days=d.weekday())


def _counts_by_area(session, start, end):
    rows = (
        session.query(Incident.npa, func.count())
        .filter(Incident.date_reported >= _dt(start), Incident.date_reported < _dt(end), Incident.npa.isnot(None))
        .group_by(Incident.npa)
        .all()
    )
    return {str(npa): n for npa, n in rows}


def _daily_counts(session, start, end):
    rows = (
        session.query(func.date(Incident.date_reported), func.count())
        .filter(Incident.date_reported >= _dt(start), Incident.date_reported < _dt(end))
        .group_by(func.date(Incident.date_reported))
        .all()
    )
    return {str(day): n for day, n in rows}


def _top(counts, n=TOP_N):
    return [npa for npa, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def make_predictions(week_start, known_through):
    """Everything Precog would have said about `week_start`'s week using data up to `known_through`."""
    from app.analysis.anomaly import detect_anomalies
    from app.analysis.forecaster import forecast_ml
    from app.analysis.risk_score import compute_risk_scores

    cut = min(week_start, known_through + timedelta(days=1))
    week_days = {str(week_start + timedelta(days=i)) for i in range(7)}

    with as_of(_dt(cut)):
        risk = compute_risk_scores(days_back=90)
        forecast = forecast_ml(days_back=365, forecast_days=(week_start - cut).days + 7)
        anomalies = detect_anomalies(days_back=7, threshold_sigma=2.0)
        session = SessionLocal()
        try:
            last_4_weeks = _counts_by_area(session, cut - timedelta(days=28), cut)
            last_8_weeks = _counts_by_area(session, cut - timedelta(days=56), cut)
            last_7_days = _daily_counts(session, cut - timedelta(days=7), cut)
        finally:
            session.close()

    spike_areas = sorted({str(a["npa"]) for a in anomalies if a["direction"] == "spike"})
    return {
        "top_areas": [
            {"npa": str(r["npa"]), "risk_score": r["risk_score"], "top_factor": r["top_factor"]}
            for r in risk[:TOP_N]
        ],
        "busiest_areas": _top(last_4_weeks),
        "forecast": [
            {"date": f["date"], "predicted": f["predicted_count"]}
            for f in forecast.get("forecast", []) if f["date"] in week_days
        ],
        "naive_daily": round(sum(last_7_days.values()) / 7, 1),
        "typical_week": {npa: round(n / 8, 2) for npa, n in last_8_weeks.items()},
        "alert_areas": spike_areas,
    }


def score_week(week_start, predictions):
    session = SessionLocal()
    try:
        actual = _counts_by_area(session, week_start, week_start + timedelta(days=7))
        daily = _daily_counts(session, week_start, week_start + timedelta(days=7))
    finally:
        session.close()

    total = sum(actual.values())
    actual_top = set(_top(actual))
    precog = [a["npa"] for a in predictions["top_areas"]]
    busiest = predictions["busiest_areas"]

    def share(areas):
        return round(sum(actual.get(a, 0) for a in areas) / total * 100, 1) if total else None

    forecast_rows = [
        {"date": f["date"], "predicted": f["predicted"], "actual": daily.get(f["date"], 0)}
        for f in predictions["forecast"]
    ]
    naive = predictions["naive_daily"]
    mae = naive_mae = None
    if forecast_rows:
        mae = round(sum(abs(r["predicted"] - r["actual"]) for r in forecast_rows) / len(forecast_rows), 1)
        naive_mae = round(sum(abs(naive - r["actual"]) for r in forecast_rows) / len(forecast_rows), 1)

    typical = predictions["typical_week"]
    alert_areas = predictions["alert_areas"]
    alert_hits = [a for a in alert_areas if actual.get(a, 0) > typical.get(a, 0)]
    comparable = [a for a, t in typical.items() if t > 0]
    any_area_rate = (
        round(sum(1 for a in comparable if actual.get(a, 0) > typical[a]) / len(comparable) * 100, 1)
        if comparable else None
    )

    return {
        "total_incidents": total,
        "precog_share": share(precog),
        "busiest_share": share(busiest),
        "precog_overlap": len(actual_top & set(precog)),
        "busiest_overlap": len(actual_top & set(busiest)),
        "area_actuals": {a: actual.get(a, 0) for a in set(precog) | set(busiest)},
        "forecast": forecast_rows,
        "forecast_mae": mae,
        "naive_mae": naive_mae,
        "alerts_raised": len(alert_areas),
        "alerts_confirmed": len(alert_hits),
        "any_area_rate": any_area_rate,
    }


def update_accuracy(backfill_weeks=BACKFILL_WEEKS):
    """Save predictions for weeks that lack them and score every week CMPD has finished publishing."""
    init_accuracy_db()
    reset_data_through()
    latest = data_through()
    if latest is None:
        return {"created": 0, "scored": 0}

    today = _today()
    scorable_through = latest - timedelta(days=PUBLICATION_LAG_DAYS)
    last_scorable = _monday_on_or_before(scorable_through - timedelta(days=6))
    next_week = _monday_on_or_before(today) + timedelta(days=7)

    weeks = []
    w = last_scorable - timedelta(days=7 * (backfill_weeks - 1))
    while w <= next_week:
        weeks.append(w)
        w += timedelta(days=7)

    created = scored = 0
    session = SessionLocal()
    try:
        existing = {r.week_start: r for r in session.query(AccuracyWeek).all()}
        for week in weeks:
            row = existing.get(week)
            upcoming = week > today
            known_through = min(week - timedelta(days=1), latest)

            stale_live = row is not None and row.mode == "live" and upcoming and row.data_through < known_through
            if row is None or stale_live:
                predictions = make_predictions(week, known_through)
                if row is None:
                    row = AccuracyWeek(week_start=week, created_at=datetime.utcnow())
                    session.add(row)
                    created += 1
                row.mode = "live" if upcoming else "reconstructed"
                row.data_through = known_through
                row.predictions = json.dumps(predictions)
                session.commit()

            if row.results is None and week <= last_scorable:
                row.results = json.dumps(score_week(week, json.loads(row.predictions)))
                row.scored_at = datetime.utcnow()
                session.commit()
                scored += 1
    finally:
        session.close()
    log.info("accuracy update: %s weeks saved, %s scored", created, scored)
    return {"created": created, "scored": scored}


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def accuracy_report():
    from app.analysis.npa_labels import get_npa_labels, label_for

    init_accuracy_db()
    session = SessionLocal()
    try:
        rows = session.query(AccuracyWeek).order_by(AccuracyWeek.week_start.desc()).all()
        rows = [(r.week_start, r.mode, r.data_through, json.loads(r.predictions),
                 json.loads(r.results) if r.results else None) for r in rows]
    finally:
        session.close()

    labels = get_npa_labels()

    def area(npa, extra=None):
        out = {"npa": npa, "label": label_for(npa, labels)}
        out.update(extra or {})
        return out

    scored, pending = [], []
    for week_start, mode, through, pred, res in rows:
        base = {
            "week_start": str(week_start),
            "week_end": str(week_start + timedelta(days=6)),
            "mode": mode,
            "data_through": str(through),
        }
        if res is None:
            base["top_areas"] = [area(a["npa"], {"risk_score": a["risk_score"], "top_factor": a["top_factor"]})
                                 for a in pred["top_areas"]]
            base["forecast"] = pred["forecast"]
            base["alerts_raised"] = len(pred["alert_areas"])
            pending.append(base)
            continue
        base.update({k: res[k] for k in (
            "total_incidents", "precog_share", "busiest_share", "precog_overlap", "busiest_overlap",
            "forecast", "forecast_mae", "naive_mae", "alerts_raised", "alerts_confirmed", "any_area_rate",
        )})
        base["top_areas"] = [
            area(a["npa"], {"risk_score": a["risk_score"], "actual": res["area_actuals"].get(a["npa"], 0)})
            for a in pred["top_areas"]
        ]
        base["busiest_areas"] = [area(a, {"actual": res["area_actuals"].get(a, 0)}) for a in pred["busiest_areas"]]
        scored.append(base)

    raised = sum(w["alerts_raised"] for w in scored)
    confirmed = sum(w["alerts_confirmed"] for w in scored)
    both = [w for w in scored if w["precog_share"] is not None and w["busiest_share"] is not None]
    fc = [w for w in scored if w["forecast_mae"] is not None]
    latest = data_through()
    return {
        "data_through": str(latest) if latest else None,
        "top_n": TOP_N,
        "summary": {
            "weeks_scored": len(scored),
            "live_weeks": sum(1 for w in scored if w["mode"] == "live"),
            "precog_share": _avg(w["precog_share"] for w in both),
            "busiest_share": _avg(w["busiest_share"] for w in both),
            "area_weeks_won": sum(1 for w in both if w["precog_share"] > w["busiest_share"]),
            "area_weeks": len(both),
            "forecast_mae": _avg(w["forecast_mae"] for w in fc),
            "naive_mae": _avg(w["naive_mae"] for w in fc),
            "forecast_weeks_won": sum(1 for w in fc if w["forecast_mae"] < w["naive_mae"]),
            "forecast_weeks": len(fc),
            "avg_daily": _avg(w["total_incidents"] / 7 for w in scored),
            "alerts_raised": raised,
            "alerts_confirmed": confirmed,
            "alert_rate": round(confirmed / raised * 100, 1) if raised else None,
            "any_area_rate": _avg(w["any_area_rate"] for w in scored),
        },
        "scored": scored,
        "pending": pending,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(update_accuracy())
