from contextlib import contextmanager
from app.models.incident import SessionLocal


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def classify_trend(recent, prior, up_ratio=1.1, down_ratio=0.9):
    if prior <= 0:
        return "stable"
    if recent > prior * up_ratio:
        return "increasing"
    if recent < prior * down_ratio:
        return "decreasing"
    return "stable"


def haversine_km(lat1, lon1, lat2, lon2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
