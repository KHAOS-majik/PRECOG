import numpy as np
from sklearn.neighbors import KernelDensity
from sqlalchemy import func
from datetime import datetime, timedelta
from app.models.incident import Incident, SessionLocal


def get_incident_coords(days_back: int = 90, nibrs_code: str = None):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = session.query(Incident.latitude, Incident.longitude).filter(
            Incident.latitude.isnot(None),
            Incident.longitude.isnot(None),
            Incident.date_reported >= cutoff,
        )
        if nibrs_code:
            q = q.filter(Incident.nibrs_code == nibrs_code)
        return np.array(q.all(), dtype=np.float64)
    finally:
        session.close()


def compute_hotspots(days_back: int = 90, nibrs_code: str = None, grid_size: int = 50, bandwidth: float = 0.005):
    coords = get_incident_coords(days_back, nibrs_code)
    if len(coords) < 10:
        return {"grid": [], "bounds": None, "incident_count": len(coords)}

    lat_min, lat_max = coords[:, 0].min(), coords[:, 0].max()
    lon_min, lon_max = coords[:, 1].min(), coords[:, 1].max()

    lat_pad = (lat_max - lat_min) * 0.05
    lon_pad = (lon_max - lon_min) * 0.05
    lat_min -= lat_pad
    lat_max += lat_pad
    lon_min -= lon_pad
    lon_max += lon_pad

    kde = KernelDensity(bandwidth=bandwidth, kernel="gaussian", metric="haversine")
    kde.fit(np.radians(coords))

    lat_grid = np.linspace(lat_min, lat_max, grid_size)
    lon_grid = np.linspace(lon_min, lon_max, grid_size)
    lat_mesh, lon_mesh = np.meshgrid(lat_grid, lon_grid)
    grid_points = np.column_stack([lat_mesh.ravel(), lon_mesh.ravel()])

    log_density = kde.score_samples(np.radians(grid_points))
    density = np.exp(log_density)

    d_min, d_max = density.min(), density.max()
    if d_max > d_min:
        risk = (density - d_min) / (d_max - d_min)
    else:
        risk = np.zeros_like(density)

    grid_data = []
    for i, (lat, lon, r) in enumerate(zip(grid_points[:, 0], grid_points[:, 1], risk)):
        if r > 0.1:
            grid_data.append({"lat": round(lat, 6), "lon": round(lon, 6), "risk": round(float(r), 4)})

    return {
        "grid": grid_data,
        "bounds": {
            "lat_min": round(lat_min, 6), "lat_max": round(lat_max, 6),
            "lon_min": round(lon_min, 6), "lon_max": round(lon_max, 6),
        },
        "incident_count": len(coords),
    }


def get_top_hotspot_areas(days_back: int = 90, top_n: int = 10):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(
                Incident.npa,
                Incident.division_id,
                func.count().label("count"),
                func.avg(Incident.latitude).label("avg_lat"),
                func.avg(Incident.longitude).label("avg_lon"),
            )
            .filter(Incident.date_reported >= cutoff, Incident.npa.isnot(None))
            .group_by(Incident.npa, Incident.division_id)
            .order_by(func.count().desc())
            .limit(top_n)
            .all()
        )
        return [
            {
                "npa": r.npa,
                "division_id": r.division_id,
                "incident_count": r.count,
                "center_lat": round(float(r.avg_lat), 6) if r.avg_lat else None,
                "center_lon": round(float(r.avg_lon), 6) if r.avg_lon else None,
            }
            for r in rows
        ]
    finally:
        session.close()
