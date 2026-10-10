import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.service_request import ServiceRequest, init_sr_db
from app.models.incident import engine

CE_ENDPOINT = "https://gis.charlottenc.gov/arcgis/rest/services/HNS/CodeEnforcementCasesAll/MapServer/0/query"
BATCH_SIZE = 2000


def get_count(where="1=1"):
    params = {"where": where, "returnCountOnly": "true", "f": "json"}
    r = requests.get(CE_ENDPOINT, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("count", 0)


def fetch_batch(limit=2000, offset=0, where="1=1"):
    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": limit,
        "resultOffset": offset,
        "orderByFields": "DateCreated DESC",
        "f": "json",
    }
    r = requests.get(CE_ENDPOINT, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    features = data.get("features", [])
    records = []
    for f in features:
        attrs = f.get("attributes", {})
        geom = f.get("geometry", {})
        attrs["_x"] = geom.get("x")
        attrs["_y"] = geom.get("y")
        records.append(attrs)
    return records


def _to_python(val):
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    return val


def _epoch_to_dt(val):
    if val is None:
        return None
    try:
        return datetime.utcfromtimestamp(val / 1000)
    except Exception:
        return None


def save_batch(raw_records):
    if not raw_records:
        return 0
    records = []
    for r in raw_records:
        case_num = r.get("CaseNumber")
        if not case_num:
            continue
        lat = _to_python(r.get("_y"))
        lon = _to_python(r.get("_x"))
        records.append({
            "case_number": str(case_num),
            "case_type": _to_python(r.get("CaseType")),
            "full_address": _to_python(r.get("FullAddress")),
            "case_origin": _to_python(r.get("CaseOrigin")),
            "npa": _to_python(r.get("NPA")),
            "council_district": _to_python(r.get("CouncilDistrict")),
            "description": _to_python(r.get("DetailedDescription")),
            "case_status": _to_python(r.get("CaseStatus")),
            "conclusion": _to_python(r.get("Conclusion")),
            "date_created": _epoch_to_dt(r.get("DateCreated")),
            "date_closed": _epoch_to_dt(r.get("DateClosed")),
            "latitude": lat,
            "longitude": lon,
        })
    if not records:
        return 0
    with engine.begin() as conn:
        stmt = sqlite_insert(ServiceRequest).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["case_number"])
        result = conn.execute(stmt)
        return result.rowcount


def ingest_311(years_back=5):
    init_sr_db()
    cutoff = datetime.utcnow() - timedelta(days=years_back * 365)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    where = f"DateCreated >= TIMESTAMP '{cutoff_str}'"

    total_available = get_count(where)
    print(f"[{datetime.now()}] 311/Code enforcement: {total_available} records in last {years_back} years")

    total_added = 0
    offset = 0

    while offset < total_available:
        batch = min(BATCH_SIZE, total_available - offset)
        try:
            raw = fetch_batch(limit=batch, offset=offset, where=where)
        except Exception as e:
            print(f"  Error at offset {offset}: {e}")
            break
        if not raw:
            break
        saved = save_batch(raw)
        total_added += saved
        offset += len(raw)
        if offset % 20000 == 0 or offset >= total_available:
            print(f"  Progress: {offset}/{total_available} ({offset*100//total_available}%) — {total_added} added")

    print(f"[{datetime.now()}] Done: {total_added} code enforcement cases added")
    return {"added": total_added, "total_fetched": offset}


if __name__ == "__main__":
    ingest_311()
