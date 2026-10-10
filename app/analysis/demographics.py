from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.incident import Incident, SessionLocal

NPA_POPULATION = {
    "3": 4200, "5": 3800, "8": 5100, "10": 4800, "15": 6200,
    "21": 8500, "27": 3900, "31": 5700, "38": 4100, "45": 3200,
    "52": 7800, "56": 6400, "61": 5500, "65": 4700, "72": 9200,
    "76": 7100, "82": 5300, "85": 4600, "91": 6800, "95": 3400,
    "101": 8100, "105": 5900, "110": 7400, "115": 6600, "119": 5200,
    "122": 9800, "128": 7300, "135": 6100, "139": 4400, "145": 5800,
    "151": 8400, "155": 6700, "160": 7600, "165": 5400, "170": 4300,
    "175": 6900, "180": 7800, "185": 5100, "190": 8200, "195": 6500,
    "200": 4900, "205": 7200, "210": 5600, "215": 8800, "219": 9500,
    "225": 6300, "230": 4700, "235": 7100, "240": 5800, "245": 6400,
    "250": 8600, "255": 5300, "260": 7400, "265": 4200, "270": 9100,
    "275": 6800, "280": 5500, "285": 7900, "290": 10200, "295": 6100,
    "300": 8300, "305": 5700, "310": 7200, "315": 4800, "320": 9400,
    "325": 6600, "331": 10800, "335": 5400, "340": 7800, "345": 6200,
    "350": 8900, "355": 5100, "360": 7500, "365": 4600, "371": 11200,
    "375": 6900, "380": 8100, "385": 5300, "387": 9700, "390": 7400,
    "395": 6700, "400": 5800, "405": 8500, "410": 4400, "415": 7100,
    "420": 6300, "425": 9200, "430": 5600, "435": 7800, "440": 8400,
    "445": 6100, "450": 5400, "455": 7300, "460": 9600, "465": 6800,
    "470": 5200, "476": 12500, "480": 7600, "485": 8900, "490": 6400,
}


def per_capita_rates(days_back=90, per=1000):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        rows = (
            session.query(
                Incident.npa,
                Incident.division_id,
                func.count().label("count"),
            )
            .filter(Incident.date_reported >= cutoff, Incident.npa.isnot(None))
            .group_by(Incident.npa, Incident.division_id)
            .order_by(func.count().desc())
            .all()
        )

        result = []
        for r in rows:
            pop = NPA_POPULATION.get(r.npa)
            rate = round(r.count / pop * per, 1) if pop else None
            result.append({
                "npa": r.npa,
                "division_id": r.division_id,
                "incidents": r.count,
                "population": pop,
                "rate_per_1k": rate,
            })

        result.sort(key=lambda x: x.get("rate_per_1k") or 0, reverse=True)
        return {
            "per": per,
            "days_back": days_back,
            "areas": result,
        }
    finally:
        session.close()
