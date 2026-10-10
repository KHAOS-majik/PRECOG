import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import func
from sklearn.ensemble import GradientBoostingRegressor
from app.analysis.features import build_daily_features, to_feature_vector, build_future_features, FEATURE_NAMES
from app.analysis.model_cache import get_or_train
from app.models.incident import Incident, SessionLocal

TOP_CRIME_TYPES = [
    ("23F", "Theft From Motor Vehicle"),
    ("13B", "Simple Assault"),
    ("23H", "All Other Thefts"),
    ("240", "Motor Vehicle Theft"),
    ("23C", "Shoplifting"),
    ("290", "Damage/Vandalism"),
    ("35A", "Drug/Narcotic Violations"),
    ("220", "Burglary/B&E"),
    ("13A", "Aggravated Assault"),
    ("120", "Robbery"),
]

TEST_DAYS = 30


def _train_type_model(nibrs_code, days_back=730):
    records = build_daily_features(days_back, nibrs_code=nibrs_code)
    if len(records) < 90:
        return None, {"error": "insufficient_data", "nibrs_code": nibrs_code}

    split = len(records) - TEST_DAYS
    X = np.array([to_feature_vector(r) for r in records])
    y = np.array([r["count"] for r in records])

    model = GradientBoostingRegressor(
        n_estimators=150, max_depth=4, learning_rate=0.1,
        subsample=0.8, random_state=42,
    )
    model.fit(X[:split], y[:split])

    train_r2 = round(model.score(X[:split], y[:split]), 3)
    test_r2 = round(model.score(X[split:], y[split:]), 3)

    return model, {
        "nibrs_code": nibrs_code,
        "train_r2": train_r2,
        "test_r2": test_r2,
        "records": records,
        "n_records": len(records),
    }


def forecast_by_type(days_back=730, forecast_days=7):
    results = []

    for nibrs_code, label in TOP_CRIME_TYPES:
        cache_key = f"type_forecast_{nibrs_code}_{days_back}"

        def train_fn(code=nibrs_code):
            return _train_type_model(code, days_back)

        model, meta = get_or_train(cache_key, train_fn, max_age_hours=6)
        if model is None:
            continue

        records = meta["records"]
        recent_counts = [r["count"] for r in records]
        last_rec = records[-1]

        forecast = []
        for i in range(1, forecast_days + 1):
            future_rec = build_future_features(last_rec, recent_counts, forecast, i)
            pred = max(0, float(model.predict([to_feature_vector(future_rec)])[0]))
            forecast.append({"date": str(future_rec["date"]), "predicted_count": round(pred, 1)})

        forecast_avg = np.mean([f["predicted_count"] for f in forecast])
        recent_14 = np.mean(recent_counts[-14:])
        prior_14 = np.mean(recent_counts[-28:-14]) if len(recent_counts) >= 28 else recent_14

        from app.db import classify_trend
        trend = classify_trend(recent_14, prior_14)

        pct_change = round((recent_14 - prior_14) / max(prior_14, 0.1) * 100, 1)

        results.append({
            "nibrs_code": nibrs_code,
            "description": label,
            "forecast": forecast,
            "forecast_avg": round(forecast_avg, 1),
            "recent_avg": round(recent_14, 1),
            "prior_avg": round(prior_14, 1),
            "trend": trend,
            "pct_change": pct_change,
            "test_r2": meta["test_r2"],
        })

    results.sort(key=lambda r: abs(r["pct_change"]), reverse=True)
    return results


def type_forecast_by_npa(nibrs_code, days_back=365, top_n=10):
    """Which NPAs are seeing the most of a given crime type, and trending."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(days=days_back)
        mid = now - timedelta(days=days_back // 2)

        recent_rows = (
            session.query(Incident.npa, func.count().label("c"))
            .filter(
                Incident.date_reported >= mid,
                Incident.nibrs_code == nibrs_code,
                Incident.npa.isnot(None),
            )
            .group_by(Incident.npa)
            .order_by(func.count().desc())
            .limit(top_n)
            .all()
        )

        prior_rows = (
            session.query(Incident.npa, func.count().label("c"))
            .filter(
                Incident.date_reported >= cutoff,
                Incident.date_reported < mid,
                Incident.nibrs_code == nibrs_code,
                Incident.npa.isnot(None),
            )
            .group_by(Incident.npa)
            .all()
        )
        prior_map = {r.npa: r.c for r in prior_rows}

        results = []
        for r in recent_rows:
            prior = prior_map.get(r.npa, 0)
            delta = r.c - prior
            pct = round(delta / max(prior, 1) * 100, 1)
            results.append({
                "npa": r.npa,
                "recent_count": r.c,
                "prior_count": prior,
                "delta": delta,
                "pct_change": pct,
            })

        return {
            "nibrs_code": nibrs_code,
            "npas": results,
        }
    finally:
        session.close()
