import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

CMPD_ENDPOINT = "https://gis.charlottenc.gov/arcgis/rest/services/CMPD/CMPDIncidents/MapServer/0/query"

def fetch_incidents(limit: int = 1000, offset: int = 0) -> list[dict]:
    params = {
        "where": "1=1",
        "outFields": "*",
        "resultRecordCount": limit,
        "resultOffset": offset,
        "orderByFields": "DATE_REPORTED DESC",
        "f": "json"
    }
    response = requests.get(CMPD_ENDPOINT, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    features = data.get("features", [])
    return [f["attributes"] for f in features]

def normalize_incidents(raw: list[dict]) -> pd.DataFrame:
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

def run_ingestion(limit: int = 1000) -> pd.DataFrame:
    print(f"[{datetime.now()}] Fetching {limit} incidents from CMPD...")
    raw = fetch_incidents(limit=limit)
    print(f"  Retrieved {len(raw)} records.")
    df = normalize_incidents(raw)
    print(f"  Normalized to {len(df)} valid records.")
    return df

if __name__ == "__main__":
    df = run_ingestion(limit=500)
    print(df.head())
    print(f"\nColumns: {list(df.columns)}")
    print(f"Date range: {df['DATE_REPORTED'].min()} → {df['DATE_REPORTED'].max()}")
