import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal

CHARLOTTE_POIS = {
    "schools": [
        {"name": "Olympic High School", "lat": 35.1790, "lon": -80.8220},
        {"name": "West Charlotte High School", "lat": 35.2430, "lon": -80.8930},
        {"name": "Harding University High School", "lat": 35.1970, "lon": -80.8870},
        {"name": "Garinger High School", "lat": 35.2380, "lon": -80.7990},
        {"name": "North Mecklenburg High School", "lat": 35.3370, "lon": -80.8380},
        {"name": "South Mecklenburg High School", "lat": 35.1590, "lon": -80.8500},
        {"name": "East Mecklenburg High School", "lat": 35.1890, "lon": -80.7870},
        {"name": "Myers Park High School", "lat": 35.1740, "lon": -80.8310},
        {"name": "Independence High School", "lat": 35.1680, "lon": -80.7530},
        {"name": "Vance High School", "lat": 35.3010, "lon": -80.8110},
        {"name": "Ardrey Kell High School", "lat": 35.0880, "lon": -80.8240},
        {"name": "Butler High School", "lat": 35.0700, "lon": -80.7560},
        {"name": "Hopewell High School", "lat": 35.3350, "lon": -80.7610},
        {"name": "Mallard Creek High School", "lat": 35.3240, "lon": -80.7340},
        {"name": "Julius Chambers High School", "lat": 35.2980, "lon": -80.7550},
    ],
    "transit_hubs": [
        {"name": "Charlotte Transportation Center", "lat": 35.2260, "lon": -80.8430},
        {"name": "CTC/Arena Station", "lat": 35.2250, "lon": -80.8440},
        {"name": "3rd St/Convention Center", "lat": 35.2230, "lon": -80.8460},
        {"name": "Stonewall Station", "lat": 35.2190, "lon": -80.8480},
        {"name": "Carson Station", "lat": 35.2130, "lon": -80.8530},
        {"name": "Bland Station", "lat": 35.2090, "lon": -80.8560},
        {"name": "East/West Station", "lat": 35.2050, "lon": -80.8580},
        {"name": "New Bern Station", "lat": 35.2010, "lon": -80.8610},
        {"name": "Scaleybark Station", "lat": 35.1930, "lon": -80.8650},
        {"name": "Woodlawn Station", "lat": 35.1820, "lon": -80.8710},
        {"name": "Tyvola Station", "lat": 35.1740, "lon": -80.8760},
        {"name": "Archdale Station", "lat": 35.1650, "lon": -80.8810},
        {"name": "Arrowood Station", "lat": 35.1530, "lon": -80.8870},
        {"name": "Sharon Rd West Station", "lat": 35.1420, "lon": -80.8890},
        {"name": "I-485/South Blvd Station", "lat": 35.1070, "lon": -80.8830},
        {"name": "UNC Charlotte Main Station", "lat": 35.3090, "lon": -80.7330},
        {"name": "JW Clay Blvd Station", "lat": 35.3170, "lon": -80.7430},
        {"name": "University City Blvd Station", "lat": 35.3080, "lon": -80.7550},
        {"name": "McCullough Station", "lat": 35.2980, "lon": -80.7600},
        {"name": "Tom Hunter Station", "lat": 35.2850, "lon": -80.7690},
        {"name": "Old Concord Rd Station", "lat": 35.2700, "lon": -80.7750},
        {"name": "Sugar Creek Station", "lat": 35.2560, "lon": -80.7990},
        {"name": "36th St Station", "lat": 35.2450, "lon": -80.8080},
        {"name": "25th St Station", "lat": 35.2380, "lon": -80.8170},
        {"name": "Parkwood Station", "lat": 35.2340, "lon": -80.8230},
        {"name": "9th St Station", "lat": 35.2290, "lon": -80.8370},
    ],
    "nightlife_areas": [
        {"name": "South End", "lat": 35.2110, "lon": -80.8570},
        {"name": "NoDa (North Davidson)", "lat": 35.2500, "lon": -80.8130},
        {"name": "Plaza Midwood", "lat": 35.2210, "lon": -80.7980},
        {"name": "Uptown/EpiCentre", "lat": 35.2270, "lon": -80.8420},
        {"name": "Camp North End", "lat": 35.2500, "lon": -80.8500},
        {"name": "LoSo (Lower South End)", "lat": 35.1990, "lon": -80.8610},
        {"name": "Montford Drive", "lat": 35.1880, "lon": -80.8270},
        {"name": "Elizabeth", "lat": 35.2170, "lon": -80.8190},
    ],
}


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def proximity_analysis(days_back=90, radius_km=0.5, nibrs_code=None):
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
        incidents = q.all()

        coords = np.array([[i.latitude, i.longitude] for i in incidents])
        results = {}

        for category, pois in CHARLOTTE_POIS.items():
            poi_results = []
            for poi in pois:
                distances = np.array([
                    _haversine_km(poi["lat"], poi["lon"], c[0], c[1])
                    for c in coords
                ])
                within = int(np.sum(distances <= radius_km))
                if within > 0:
                    poi_results.append({
                        "name": poi["name"],
                        "lat": poi["lat"],
                        "lon": poi["lon"],
                        "incidents_within": within,
                        "density_per_sq_km": round(within / (np.pi * radius_km ** 2), 1),
                    })
            poi_results.sort(key=lambda p: p["incidents_within"], reverse=True)
            results[category] = poi_results

        return {
            "radius_km": radius_km,
            "days_back": days_back,
            "total_incidents": len(coords),
            "categories": results,
        }
    finally:
        session.close()
