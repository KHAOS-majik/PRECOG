import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.incident import Incident, SessionLocal, init_db, engine

load_dotenv()

CMPD_ENDPOINT = "https://gis.charlottenc.gov/arcgis/rest/services/CMPD/CMPDIncidents/MapServer/0/query"
BATCH_SIZE = 2000


def fetch_incidents(limit=1000, offset=0, where="1=1"):
    params = {
        "where": where,
        "outFields": "*",
        "resultRecordCount": limit,
        "resultOffset": offset,
        "orderByFields": "DATE_REPORTED DESC",
        "f": "json"
    }
    response = requests.get(CMPD_ENDPOINT, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def get_record_count(where="1=1"):
    params = {"where": where, "returnCountOnly": "true", "f": "json"}
    r = requests.get(CMPD_ENDPOINT, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("count", 0)


def normalize_incidents(raw):
    df = pd.DataFrame(raw)
    for col in ["DATE_REPORTED", "DATE_INCIDENT_BEGAN", "DATE_INCIDENT_END", "CLEARANCE_DATE"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
    for col in ["LATITUDE_PUBLIC", "LONGITUDE_PUBLIC", "X_COORD_PUBLIC", "Y_COORD_PUBLIC"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["LATITUDE_PUBLIC", "LONGITUDE_PUBLIC"], inplace=True)
    df.drop_duplicates(subset=["INCIDENT_REPORT_ID"], inplace=True)
    return df


def _to_python(val):
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return val.to_pydatetime()
    if pd.isna(val):
        return None
    return val


def save_incidents(df):
    records = []
    for _, row in df.iterrows():
        records.append({
            "incident_report_id": str(row.get("INCIDENT_REPORT_ID", "")),
            "date_reported": _to_python(row.get("DATE_REPORTED")),
            "date_incident_began": _to_python(row.get("DATE_INCIDENT_BEGAN")),
            "date_incident_end": _to_python(row.get("DATE_INCIDENT_END")),
            "clearance_date": _to_python(row.get("CLEARANCE_DATE")),
            "address": _to_python(row.get("LOCATION")),
            "city": _to_python(row.get("CITY")),
            "state": _to_python(row.get("STATE")),
            "zip_code": str(row.get("ZIP")) if _to_python(row.get("ZIP")) else None,
            "latitude": _to_python(row.get("LATITUDE_PUBLIC")),
            "longitude": _to_python(row.get("LONGITUDE_PUBLIC")),
            "nibrs_code": _to_python(row.get("HIGHEST_NIBRS_CODE")),
            "nibrs_code_group": None,
            "nibrs_offense_description": _to_python(row.get("HIGHEST_NIBRS_DESCRIPTION")),
            "clearance_status": _to_python(row.get("CLEARANCE_STATUS")),
            "clearance_detail_status": _to_python(row.get("CLEARANCE_DETAIL_STATUS")),
            "division_id": str(row.get("DIVISION_ID")) if _to_python(row.get("DIVISION_ID")) else None,
            "npa": str(int(row.get("NPA"))) if _to_python(row.get("NPA")) is not None else None,
            "place_type_description": _to_python(row.get("PLACE_TYPE_DESCRIPTION")),
            "cmpd_patrol_division": _to_python(row.get("CMPD_PATROL_DIVISION")),
            "location_type": _to_python(row.get("LOCATION_TYPE_DESCRIPTION")),
            "place_detail": _to_python(row.get("PLACE_DETAIL_DESCRIPTION")),
        })
    if not records:
        return 0
    with engine.begin() as conn:
        stmt = sqlite_insert(Incident).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["incident_report_id"])
        result = conn.execute(stmt)
        return result.rowcount


def ingest(limit=1000):
    init_db()
    where = "1=1"
    total_available = get_record_count(where)
    target = min(limit, total_available)
    print(f"[{datetime.now()}] {total_available} records available, fetching {target}...")

    total_added = 0
    total_skipped = 0
    offset = 0

    while offset < target:
        batch = min(BATCH_SIZE, target - offset)
        try:
            raw = fetch_incidents(limit=batch, offset=offset, where=where)
        except Exception as e:
            print(f"  Error at offset {offset}: {e}")
            break
        if not raw:
            break
        df = normalize_incidents(raw)
        saved = save_incidents(df)
        total_added += saved
        total_skipped += len(df) - saved
        offset += len(raw)
        print(f"  Progress: {offset}/{target} fetched, {total_added} added, {total_skipped} skipped")

    print(f"[{datetime.now()}] Done: {total_added} added, {total_skipped} skipped")
    return {"added": total_added, "skipped": total_skipped}


def ingest_historical(years_back=5):
    init_db()
    cutoff = datetime.utcnow() - timedelta(days=years_back * 365)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    where = f"DATE_REPORTED >= TIMESTAMP '{cutoff_str}'"

    total_available = get_record_count(where)
    print(f"[{datetime.now()}] Historical ingest: {total_available} records in last {years_back} years")

    total_added = 0
    total_skipped = 0
    offset = 0

    while offset < total_available:
        batch = min(BATCH_SIZE, total_available - offset)
        try:
            raw = fetch_incidents(limit=batch, offset=offset, where=where)
        except Exception as e:
            print(f"  Error at offset {offset}: {e}")
            break
        if not raw:
            break
        df = normalize_incidents(raw)
        saved = save_incidents(df)
        total_added += saved
        total_skipped += len(df) - saved
        offset += len(raw)
        if offset % 10000 == 0 or offset >= total_available:
            print(f"  Progress: {offset}/{total_available} ({offset*100//total_available}%) — {total_added} added")

    print(f"[{datetime.now()}] Done: {total_added} added, {total_skipped} skipped")
    return {"added": total_added, "skipped": total_skipped, "total_fetched": offset}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--historical":
        years = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        result = ingest_historical(years_back=years)
    else:
        result = ingest(limit=1000)
    print(result)
