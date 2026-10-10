import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.models.incident import init_db
from app.models.news import init_news_db
from app.models.weather import init_weather_db
from app.models.service_request import init_sr_db
from app.models.social import init_social_db
from app.api.routes import router

_stop_event = threading.Event()


def _auto_ingest_loop():
    time.sleep(5)
    while not _stop_event.is_set():
        try:
            from app.ingestion.reddit import ingest_reddit
            from app.ingestion.bluesky import ingest_bluesky
            from app.ingestion.news import ingest_news
            print("[auto-ingest] Pulling social media + news...")
            r = ingest_reddit()
            print(f"[auto-ingest] Reddit: {r['added']} new")
            b = ingest_bluesky()
            print(f"[auto-ingest] Bluesky: {b['added']} new")
            n = ingest_news()
            print(f"[auto-ingest] News: {n['added']} new")
        except Exception as e:
            print(f"[auto-ingest] Error: {e}")
        _stop_event.wait(1800)


@asynccontextmanager
async def lifespan(app):
    init_db()
    init_news_db()
    init_weather_db()
    init_sr_db()
    init_social_db()
    if os.getenv("ENABLE_AUTO_INGEST", "0") == "1":
        t = threading.Thread(target=_auto_ingest_loop, daemon=True)
        t.start()
        print("[startup] auto-ingest thread started")
    else:
        print("[startup] auto-ingest disabled (set ENABLE_AUTO_INGEST=1 to enable)")
    yield
    _stop_event.set()


app = FastAPI(title="Precog", version="0.2.0", lifespan=lifespan)
app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
