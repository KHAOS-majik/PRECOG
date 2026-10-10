# Precog

Crime incident data ingestion and analysis API for Charlotte-Mecklenburg (CMPD).

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy psycopg2-binary pandas requests python-dotenv numpy
```

Copy `.env` and configure your database URL (defaults to SQLite):

```
DATABASE_URL=sqlite:///./precog.db
```

## Run

```bash
python main.py
```

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/incidents` | List incidents (filterable by `nibrs_code`, `division_id`) |
| GET | `/api/incidents/{report_id}` | Get single incident by report ID |
| GET | `/api/stats/by-type` | Incident counts by NIBRS offense type |
| GET | `/api/stats/by-division` | Incident counts by CMPD division |
| POST | `/api/ingest?limit=5000` | Trigger CMPD data ingestion |
