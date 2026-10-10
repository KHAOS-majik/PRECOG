import os
import hashlib
import time
import joblib

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "models")


def _ensure_dir():
    os.makedirs(MODELS_DIR, exist_ok=True)


def _cache_path(key):
    safe = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(MODELS_DIR, f"{safe}.joblib")


def _meta_path(key):
    safe = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(MODELS_DIR, f"{safe}.meta")


def get_or_train(key, train_fn, max_age_hours=6):
    _ensure_dir()
    cp = _cache_path(key)
    mp = _meta_path(key)

    if os.path.exists(cp) and os.path.exists(mp):
        age_hours = (time.time() - os.path.getmtime(cp)) / 3600
        if age_hours < max_age_hours:
            meta = joblib.load(mp)
            model = joblib.load(cp)
            meta["from_cache"] = True
            meta["cache_age_hours"] = round(age_hours, 2)
            return model, meta

    model, meta = train_fn()
    meta["from_cache"] = False
    meta["trained_at"] = time.time()
    joblib.dump(model, cp)
    joblib.dump(meta, mp)
    return model, meta


def invalidate(key):
    cp = _cache_path(key)
    mp = _meta_path(key)
    for p in [cp, mp]:
        if os.path.exists(p):
            os.remove(p)


def invalidate_all():
    _ensure_dir()
    for f in os.listdir(MODELS_DIR):
        if f.endswith((".joblib", ".meta")):
            os.remove(os.path.join(MODELS_DIR, f))
