import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import func
from sklearn.ensemble import GradientBoostingRegressor
from app.analysis.features import build_daily_features, to_feature_vector, build_future_features, FEATURE_NAMES
from app.analysis.model_cache import get_or_train
from app.models.incident import Incident, SessionLocal

TEST_DAYS = 30
TOP_NPAS = 40


def _train_citywide(days_back, nibrs_code):
    records = build_daily_features(days_back, nibrs_code=nibrs_code)
    if len(records) < 60:
        return None, {"error": "insufficient_data", "n_records": len(records)}

    split = len(records) - TEST_DAYS
    X = np.array([to_feature_vector(r) for r in records])
    y = np.array([r["count"] for r in records])

    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.1,
        subsample=0.8, random_state=42,
    )
    model.fit(X_train, y_train)

    train_r2 = model.score(X_train, y_train)
    test_r2 = model.score(X_test, y_test)
    importances = dict(zip(FEATURE_NAMES, [round(float(v), 4) for v in model.feature_importances_]))

    meta = {
        "train_r2": round(train_r2, 3),
        "test_r2": round(test_r2, 3),
        "n_train": split,
        "n_test": TEST_DAYS,
        "n_records": len(records),
        "feature_importance": importances,
        "records": records,
    }
    return model, meta


def forecast_ml(days_back=365, forecast_days=14, nibrs_code=None):
    cache_key = f"citywide_{days_back}_{nibrs_code or 'all'}"

    def train_fn():
        return _train_citywide(days_back, nibrs_code)

    model, meta = get_or_train(cache_key, train_fn, max_age_hours=6)

    if model is None:
        return {"history": [], "forecast": [], "trend": "insufficient_data", "model": "none"}

    records = meta["records"]
    recent_counts = [r["count"] for r in records]
    last_rec = records[-1]

    history = [{"date": str(r["date"]), "count": r["count"]} for r in records[-90:]]

    forecast = []
    for i in range(1, forecast_days + 1):
        future_rec = build_future_features(last_rec, recent_counts, forecast, i)
        pred = max(0, float(model.predict([to_feature_vector(future_rec)])[0]))
        forecast.append({"date": str(future_rec["date"]), "predicted_count": round(pred, 1)})

    from app.db import classify_trend
    y = np.array(recent_counts)
    avg_daily = round(float(y.mean()), 1)
    recent_avg = float(y[-14:].mean())
    prior_avg = float(y[-28:-14].mean()) if len(y) >= 28 else avg_daily
    trend = classify_trend(recent_avg, prior_avg, up_ratio=1.05, down_ratio=0.95)

    return {
        "history": history,
        "forecast": forecast,
        "trend": trend,
        "avg_daily": avg_daily,
        "model": "gradient_boosting",
        "train_r2": meta["train_r2"],
        "test_r2": meta["test_r2"],
        "r2_score": meta["test_r2"],
        "feature_importance": meta["feature_importance"],
        "from_cache": meta.get("from_cache", False),
        "trained_at": meta.get("trained_at"),
        "cache_age_hours": meta.get("cache_age_hours"),
    }


def _get_top_npas(days_back=365):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(Incident.npa, func.count().label("c"))
            .filter(Incident.date_reported >= cutoff, Incident.npa.isnot(None))
            .group_by(Incident.npa)
            .order_by(func.count().desc())
            .limit(TOP_NPAS)
            .all()
        )
        return [r.npa for r in rows]
    finally:
        session.close()


def _npa_daily_counts(days_back=365):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(
                Incident.npa,
                func.date(Incident.date_reported).label("day"),
                func.count().label("count"),
            )
            .filter(Incident.date_reported >= cutoff, Incident.npa.isnot(None))
            .group_by(Incident.npa, func.date(Incident.date_reported))
            .all()
        )
        result = defaultdict(dict)
        for r in rows:
            d = datetime.strptime(str(r.day), "%Y-%m-%d").date() if isinstance(r.day, str) else r.day
            result[r.npa][d] = r.count
        return result
    finally:
        session.close()


def forecast_by_npa(days_back=365, forecast_days=7, top_n=20):
    cache_key = f"npa_forecast_{days_back}"

    def train_fn():
        top_npas = _get_top_npas(days_back)
        npa_counts = _npa_daily_counts(days_back)
        citywide_records = build_daily_features(days_back)
        if len(citywide_records) < 60:
            return None, {"error": "insufficient_data"}

        all_dates = [r["date"] for r in citywide_records]
        city_map = {r["date"]: r for r in citywide_records}

        npa_avgs = {}
        for npa in top_npas:
            counts = list(npa_counts.get(npa, {}).values())
            npa_avgs[npa] = np.mean(counts) if counts else 0

        X_all, y_all, npa_idx = [], [], []
        for npa_i, npa in enumerate(top_npas):
            dc = npa_counts.get(npa, {})
            for d in all_dates:
                city_rec = city_map.get(d)
                if not city_rec:
                    continue
                count = dc.get(d, 0)
                vec = to_feature_vector(city_rec) + [npa_i, npa_avgs[npa]]
                X_all.append(vec)
                y_all.append(count)
                npa_idx.append(npa_i)

        if len(X_all) < 100:
            return None, {"error": "insufficient_data"}

        X = np.array(X_all)
        y = np.array(y_all)

        split = len(all_dates) - TEST_DAYS
        n_dates = len(all_dates)
        train_dates = set(all_dates[:split])
        train_mask, test_mask = [], []
        for idx in range(len(X_all)):
            date_idx = idx % n_dates
            if all_dates[date_idx] in train_dates:
                train_mask.append(idx)
            else:
                test_mask.append(idx)

        if not train_mask:
            train_mask = list(range(len(X_all)))
            test_mask = train_mask[-100:]

        model = GradientBoostingRegressor(
            n_estimators=150, max_depth=4, learning_rate=0.1,
            subsample=0.8, random_state=42,
        )
        model.fit(X[train_mask], y[train_mask])

        test_r2 = model.score(X[test_mask], y[test_mask]) if test_mask else 0
        feature_names = FEATURE_NAMES + ["npa_index", "npa_avg"]

        meta = {
            "test_r2": round(test_r2, 3),
            "top_npas": top_npas,
            "npa_avgs": npa_avgs,
            "all_dates": all_dates,
            "city_records": citywide_records,
            "npa_counts": {npa: dict(npa_counts.get(npa, {})) for npa in top_npas},
            "feature_names": feature_names,
        }
        return model, meta

    model, meta = get_or_train(cache_key, train_fn, max_age_hours=6)
    if model is None:
        return []

    top_npas = meta["top_npas"][:top_n]
    npa_avgs = meta["npa_avgs"]
    citywide_records = meta["city_records"]
    npa_counts_raw = meta["npa_counts"]
    last_city = citywide_records[-1]
    recent_city_counts = [r["count"] for r in citywide_records]

    results = []
    for npa_i, npa in enumerate(top_npas):
        npa_dc = npa_counts_raw.get(npa, {})
        recent_npa = [npa_dc.get(d, 0) for d in meta["all_dates"][-28:]]
        avg_recent_14 = np.mean(recent_npa[-14:]) if recent_npa else 0
        avg_prior_14 = np.mean(recent_npa[-28:-14]) if len(recent_npa) >= 28 else avg_recent_14

        forecast = []
        for day_i in range(1, forecast_days + 1):
            future_rec = build_future_features(last_city, recent_city_counts, forecast, day_i)
            vec = to_feature_vector(future_rec) + [npa_i, npa_avgs.get(npa, 0)]
            pred = max(0, float(model.predict([vec])[0]))
            forecast.append({"date": str(future_rec["date"]), "predicted_count": round(pred, 1)})

        from app.db import classify_trend
        forecast_avg = np.mean([f["predicted_count"] for f in forecast])
        trend = classify_trend(avg_recent_14, avg_prior_14)

        results.append({
            "npa": npa,
            "forecast": forecast,
            "forecast_avg": round(forecast_avg, 1),
            "recent_avg": round(avg_recent_14, 1),
            "trend": trend,
            "historical_avg": round(npa_avgs.get(npa, 0), 1),
        })

    results.sort(key=lambda r: r["forecast_avg"], reverse=True)
    return results
