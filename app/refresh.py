"""Keeps the data and the accuracy record current while the server runs."""
import json
import logging
import os
import threading
import time
from datetime import datetime

log = logging.getLogger("precog.refresh")

CMPD_INTERVAL_S = 3600
NEWS_INTERVAL_S = 3600
SIDE_DATA_INTERVAL_S = 24 * 3600
_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "refresh_state.json")
_lock = threading.Lock()


def read_state():
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_state(**changes):
    state = read_state()
    state.update(changes)
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f)


def refresh_cmpd():
    """Pull recent CMPD reports, then save and score predictions against them."""
    from app.analysis.accuracy import update_accuracy
    from app.analysis.model_cache import invalidate_all
    from app.clock import data_through, reset_data_through
    from app.analysis.news_check import reset_cache as reset_news_cache
    from app.ingestion.cmpd import ingest_recent

    with _lock:
        result = ingest_recent(days=45)
        reset_data_through()
        reset_news_cache()
        if result["added"]:
            invalidate_all()
        accuracy = update_accuracy()
        _write_state(
            cmpd_at=time.time(),
            cmpd_added=result["added"],
            data_through=str(data_through()),
            weeks_scored=accuracy["scored"],
        )
        return result


def refresh_side_data():
    """Weather and 311 feed the risk score and forecast; daily is enough."""
    from app.ingestion.service311 import ingest_311
    from app.ingestion.weather import ingest_weather

    ingest_weather(years_back=0.2)
    ingest_311(years_back=0.2)
    _write_state(side_data_at=time.time())


def refresh_news():
    from app.analysis.news_check import reset_cache
    from app.ingestion.news import ingest_news

    result = ingest_news()
    reset_cache()
    _write_state(news_at=time.time(), news_added=result["added"])
    return result


def run_loop(stop_event):
    stop_event.wait(10)
    while not stop_event.is_set():
        state = read_state()
        now = time.time()
        try:
            if now - state.get("side_data_at", 0) >= SIDE_DATA_INTERVAL_S:
                refresh_side_data()
            if now - state.get("cmpd_at", 0) >= CMPD_INTERVAL_S:
                result = refresh_cmpd()
                log.info("CMPD refresh at %s: %s new", datetime.now(), result["added"])
            if now - state.get("news_at", 0) >= NEWS_INTERVAL_S:
                result = refresh_news()
                log.info("news refresh at %s: %s new", datetime.now(), result["added"])
        except Exception:
            log.exception("refresh failed; will retry next cycle")
            _write_state(last_error_at=time.time())
        stop_event.wait(300)
