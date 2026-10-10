import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from time import sleep
from html import unescape

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.incident import engine, SessionLocal, Incident
from app.models.social import SocialPost, init_social_db

HEADERS = {"User-Agent": "Precog:v1.0.0 (by /u/precog_bot_clt)"}
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
REQUEST_DELAY = 2

SUBREDDITS = ["Charlotte", "CharlotteNC"]

CLT_NEIGHBORHOODS = {
    "Uptown": (35.2271, -80.8431), "NoDa": (35.2470, -80.8176),
    "Plaza Midwood": (35.2190, -80.8090), "South End": (35.2108, -80.8565),
    "Dilworth": (35.2050, -80.8480), "Myers Park": (35.1880, -80.8300),
    "Eastland": (35.2020, -80.7600), "University City": (35.3080, -80.7430),
    "Ballantyne": (35.0570, -80.8480), "Steele Creek": (35.1030, -80.9470),
    "Northlake": (35.3440, -80.8860), "Mint Hill": (35.1790, -80.6470),
    "Matthews": (35.1168, -80.7237), "Pineville": (35.0832, -80.8512),
    "West Charlotte": (35.2300, -80.8900), "Hidden Valley": (35.2740, -80.7970),
    "Grier Heights": (35.1950, -80.8180), "Enderly Park": (35.2170, -80.8800),
    "Belmont": (35.2280, -80.8240), "Optimist Park": (35.2340, -80.8230),
    "Wesley Heights": (35.2260, -80.8660), "Seversville": (35.2310, -80.8610),
    "Cherry": (35.2060, -80.8400), "Camp Greene": (35.2230, -80.8840),
    "Elizabeth": (35.2130, -80.8310), "Sedgefield": (35.1930, -80.8550),
    "Cotswold": (35.1880, -80.7960), "SouthPark": (35.1550, -80.8280),
    "Derita": (35.2980, -80.8130), "Thomasboro": (35.2650, -80.8490),
    "Highland Creek": (35.3220, -80.7340), "Mallard Creek": (35.3130, -80.7570),
    "Arboretum": (35.1010, -80.7980), "Quail Hollow": (35.1190, -80.8220),
    "Idlewild": (35.1650, -80.7290), "Ashley Park": (35.2380, -80.8770),
    "Druid Hills": (35.2520, -80.8280), "Villa Heights": (35.2410, -80.8210),
    "Chantilly": (35.2260, -80.7900), "Eastover": (35.1940, -80.8250),
    "Wilmore": (35.2090, -80.8560), "Biddleville": (35.2340, -80.8680),
    "Shamrock": (35.2100, -80.7650), "Freedom Park": (35.2010, -80.8450),
    "Revolution Park": (35.2020, -80.8710), "Montclaire": (35.1440, -80.8780),
}

CLT_STREETS = [
    "Tryon", "Trade", "Independence", "Freedom", "Beatties Ford",
    "Central Ave", "South Blvd", "Nations Ford", "Albemarle",
    "Statesville", "Graham", "Brookshire", "Wilkinson", "Sugar Creek",
    "Eastway", "The Plaza", "Monroe", "Providence", "Park Rd",
    "Morehead", "Clanton", "West Blvd", "Remount", "Tuckaseegee",
    "Mt Holly", "Rozelles Ferry", "Rozzelles Ferry",
    "W.T. Harris", "Harris Blvd", "Sardis", "Sharon Amity",
    "Randolph", "Wendover", "Fairview", "Selwyn",
    "Carmel", "Rea Rd", "Ballantyne Commons",
    "Shopton", "Steele Creek", "Arrowood",
    "Billy Graham", "Woodlawn", "Tyvola", "Yorkmont",
    "Old Pineville", "South Tryon", "North Tryon",
    "I-77", "I-85", "I-485", "MLK", "Martin Luther King",
]

CRIME_KEYWORDS = [
    "shooting", "shot", "stabbing", "stabbed", "robbery", "robbed",
    "carjacking", "carjacked", "murder", "homicide", "killed",
    "assault", "attacked", "burglary", "break-in", "theft",
    "stolen", "gunshot", "gunfire", "armed", "weapon",
    "police", "cmpd", "arrest", "suspect", "victim",
    "crime", "incident", "investigation",
    "hit and run", "hit-and-run", "crash fatal",
    "missing person", "kidnapping", "rape", "sexual assault",
    "drug", "overdose", "fentanyl",
]

SEARCH_QUERIES = [
    "shooting", "crime", "CMPD", "homicide",
    "stabbing", "robbery", "carjacking",
    "gunshot", "missing person",
]


def geocode_post(text):
    if not text:
        return None, None, None
    for name, (lat, lon) in CLT_NEIGHBORHOODS.items():
        if name.lower() in text.lower():
            return lat, lon, name
    for street in CLT_STREETS:
        pattern = re.compile(
            rf"\b\d{{2,5}}\s+(?:\w+\s+)?{re.escape(street)}\b", re.IGNORECASE
        )
        match = pattern.search(text)
        if match:
            session = SessionLocal()
            try:
                incident = (
                    session.query(Incident.latitude, Incident.longitude)
                    .filter(Incident.address.ilike(f"%{street}%"))
                    .filter(Incident.latitude.isnot(None))
                    .first()
                )
                if incident:
                    return incident.latitude, incident.longitude, match.group(0)
            finally:
                session.close()
            return None, None, match.group(0)
    for street in CLT_STREETS:
        if re.search(rf"\b{re.escape(street)}\b", text, re.IGNORECASE):
            session = SessionLocal()
            try:
                incident = (
                    session.query(Incident.latitude, Incident.longitude)
                    .filter(Incident.address.ilike(f"%{street}%"))
                    .filter(Incident.latitude.isnot(None))
                    .first()
                )
                if incident:
                    return incident.latitude, incident.longitude, street
            finally:
                session.close()
    return None, None, None


def is_crime_related(text):
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in CRIME_KEYWORDS)


def match_to_incident(lat, lon, published):
    if not lat or not published:
        return None, None
    session = SessionLocal()
    try:
        lookback = published - timedelta(days=90)
        nearby = (
            session.query(Incident.date_reported)
            .filter(
                Incident.latitude.between(lat - 0.005, lat + 0.005),
                Incident.longitude.between(lon - 0.005, lon + 0.005),
                Incident.date_reported >= lookback,
                Incident.date_reported <= published + timedelta(days=1),
            )
            .order_by(Incident.date_reported.desc())
            .first()
        )
        if nearby:
            days_old = (published - nearby.date_reported).days
            return nearby.date_reported, 1 if days_old <= 30 else 0
        return None, 0
    finally:
        session.close()


def fetch_reddit_rss(subreddit, query=None, limit=25):
    if query:
        url = f"https://www.reddit.com/r/{subreddit}/search.rss"
        params = {"q": query, "restrict_sr": "on", "sort": "new", "limit": limit, "t": "month"}
    else:
        url = f"https://www.reddit.com/r/{subreddit}/new.rss"
        params = {"limit": limit}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            print(f"    Rate limited, waiting 10s...")
            sleep(10)
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            return []
    except Exception as e:
        print(f"    Reddit RSS error: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    posts = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = entry.findtext("atom:title", "", ATOM_NS)
        link_el = entry.find("atom:link", ATOM_NS)
        link = link_el.get("href", "") if link_el is not None else ""
        updated = entry.findtext("atom:updated", "", ATOM_NS)
        content_raw = entry.findtext("atom:content", "", ATOM_NS)
        content_clean = re.sub(r"<[^>]+>", "", unescape(content_raw)).strip()[:2000] if content_raw else ""
        author_el = entry.find("atom:author/atom:name", ATOM_NS)
        author = author_el.text if author_el is not None else ""

        full_text = f"{title} {content_clean}"
        if not is_crime_related(full_text):
            continue

        published = None
        if updated:
            try:
                published = datetime.fromisoformat(updated.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        post_id_match = re.search(r"/comments/(\w+)/", link)
        post_id = f"reddit_{post_id_match.group(1)}" if post_id_match else f"reddit_{hash(link)}"

        lat, lon, matched = geocode_post(full_text)
        incident_date, is_recent = None, None
        if lat:
            incident_date, is_recent = match_to_incident(lat, lon, published)

        posts.append({
            "platform": "reddit",
            "post_id": post_id,
            "author": author.replace("/u/", ""),
            "content": full_text[:2000],
            "url": link,
            "published": published,
            "search_query": query or "new",
            "subreddit": subreddit,
            "score": None,
            "num_comments": None,
            "latitude": lat,
            "longitude": lon,
            "matched_address": matched,
            "matched_incident_date": incident_date,
            "is_recent_crime": is_recent,
        })

    return posts


def save_posts(posts):
    if not posts:
        return 0
    records = [{k: v for k, v in p.items()} for p in posts]
    with engine.begin() as conn:
        stmt = sqlite_insert(SocialPost).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["post_id"])
        result = conn.execute(stmt)
        return result.rowcount


def ingest_reddit():
    init_social_db()
    total_added = 0
    total_fetched = 0

    for sub in SUBREDDITS:
        print(f"  Reddit r/{sub} new posts...")
        try:
            posts = fetch_reddit_rss(sub, limit=50)
            total_fetched += len(posts)
            saved = save_posts(posts)
            total_added += saved
            print(f"    Found {len(posts)} crime-related, saved {saved} new")
        except Exception as e:
            print(f"    Error: {e}")
        sleep(REQUEST_DELAY)

    for query in SEARCH_QUERIES[:5]:
        print(f"  Reddit r/Charlotte: {query}")
        try:
            posts = fetch_reddit_rss("Charlotte", query=query, limit=25)
            total_fetched += len(posts)
            saved = save_posts(posts)
            total_added += saved
            print(f"    Found {len(posts)} crime-related, saved {saved} new")
        except Exception as e:
            print(f"    Error: {e}")
        sleep(REQUEST_DELAY)

    session = SessionLocal()
    try:
        geocoded = session.query(SocialPost).filter(
            SocialPost.platform == "reddit",
            SocialPost.latitude.isnot(None),
        ).count()
    finally:
        session.close()

    print(f"Reddit done: {total_added} added, {geocoded} geocoded")
    return {"platform": "reddit", "added": total_added, "fetched": total_fetched, "geocoded": geocoded}


if __name__ == "__main__":
    result = ingest_reddit()
    print(result)
