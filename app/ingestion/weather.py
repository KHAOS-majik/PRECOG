import requests
from datetime import date, timedelta, datetime as dt_class
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.weather import DailyWeather, init_weather_db
from app.models.incident import engine

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
CLT_LAT = 35.2271
CLT_LON = -80.8431


def fetch_weather(start_date: str, end_date: str):
    params = {
        "latitude": CLT_LAT,
        "longitude": CLT_LON,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,rain_sum,snowfall_sum,wind_speed_10m_max,daylight_duration",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def save_weather(data):
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        return 0

    records = []
    for i, d in enumerate(dates):
        daylight_sec = daily.get("daylight_duration", [None])[i]
        records.append({
            "date": dt_class.strptime(d, "%Y-%m-%d").date(),
            "temp_max": daily.get("temperature_2m_max", [None])[i],
            "temp_min": daily.get("temperature_2m_min", [None])[i],
            "temp_mean": daily.get("temperature_2m_mean", [None])[i],
            "precipitation": daily.get("precipitation_sum", [None])[i],
            "rain": daily.get("rain_sum", [None])[i],
            "snowfall": daily.get("snowfall_sum", [None])[i],
            "wind_speed_max": daily.get("wind_speed_10m_max", [None])[i],
            "daylight_hours": round(daylight_sec / 3600, 2) if daylight_sec else None,
        })

    with engine.begin() as conn:
        stmt = sqlite_insert(DailyWeather).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["date"])
        result = conn.execute(stmt)
        return result.rowcount


def ingest_weather(years_back=5):
    init_weather_db()
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=years_back * 365)

    total_added = 0
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=365), end)
        print(f"  Fetching weather {chunk_start} to {chunk_end}")
        try:
            data = fetch_weather(str(chunk_start), str(chunk_end))
            saved = save_weather(data)
            total_added += saved
            print(f"    Saved {saved} days")
        except Exception as e:
            print(f"    Error: {e}")
        chunk_start = chunk_end + timedelta(days=1)

    print(f"Done: {total_added} weather days added")
    return {"added": total_added}


if __name__ == "__main__":
    ingest_weather()
