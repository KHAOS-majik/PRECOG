import re
import requests
from datetime import datetime, timedelta
from time import sleep

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.incident import engine, SessionLocal, Incident
from app.models.social import SocialPost, init_social_db

BSKY_API = "https://public.api.bsky.app/xrpc"
HEADERS = {"Accept": "application/json"}
REQUEST_DELAY = 1.5

CHARLOTTE_ACCOUNTS = [
    "cmpd.bsky.social",
    "wsoctv.bsky.social",
    "wcnc.bsky.social",
    "wfae.bsky.social",
]

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
    "Shamrock": (35.2100, -80.7650),
}

CLT_STREETS = [
    "Tryon", "Trade", "Independence", "Freedom", "Beatties Ford",
    "Central Ave", "South Blvd", "Nations Ford", "Albemarle",
    "Statesville", "Graham", "Brookshire", "Wilkinson", "Sugar Creek",
    "Eastway", "The Plaza", "Monroe", "Providence", "Park Rd",
    "Morehead", "Clanton", "West Blvd", "Remount", "Tuckaseegee",
    "Mt Holly", "Rozelles Ferry", "W.T. Harris", "Harris Blvd",
    "Sardis", "Sharon Amity", "Randolph", "Wendover", "Fairview",
    "Selwyn", "Carmel", "Rea Rd", "Shopton", "Steele Creek",
    "Arrowood", "Billy Graham", "Woodlawn", "Tyvola", "Yorkmont",
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
    "hit and run", "hit-and-run", "crash",
    "missing", "kidnapping", "rape",
    "drug", "overdose", "fentanyl",
]

CHARLOTTE_KEYWORDS = [
    "charlotte", "cmpd", "mecklenburg", "clt",
    "noda", "plaza midwood", "south end", "uptown",
    "dilworth", "myers park", "ballantyne", "steele creek",
    "university city", "hidden valley", "matthews", "pineville",
    "mint hill", "northlake",
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


def is_charlotte_crime(text):
    if not text:
        return False
    lower = text.lower()
    has_crime = any(kw in lower for kw in CRIME_KEYWORDS)
    has_clt = any(kw in lower for kw in CHARLOTTE_KEYWORDS)
    return has_crime and has_clt


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


def resolve_handle(handle):
    try:
        r = requests.get(f"{BSKY_API}/com.atproto.identity.resolveHandle",
            params={"handle": handle}, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("did")
    except Exception:
        pass
    return None


def fetch_author_feed(actor, limit=50, cursor=None):
    params = {"actor": actor, "limit": limit, "filter": "posts_no_replies"}
    if cursor:
        params["cursor"] = cursor
    try:
        r = requests.get(f"{BSKY_API}/app.bsky.feed.getAuthorFeed",
            params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"    Feed error for {actor}: {r.status_code}")
            return [], None
        data = r.json()
        return data.get("feed", []), data.get("cursor")
    except Exception as e:
        print(f"    Feed error for {actor}: {e}")
        return [], None


def process_feed_items(items, handle):
    posts = []
    for item in items:
        post_obj = item.get("post", {})
        record = post_obj.get("record", {})
        text = record.get("text", "")
        author_obj = post_obj.get("author", {})

        if not is_charlotte_crime(text):
            continue

        created = record.get("createdAt", "")
        published = None
        if created:
            try:
                published = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        uri = post_obj.get("uri", "")
        at_parts = uri.split("/")
        rkey = at_parts[-1] if at_parts else ""
        did = author_obj.get("did", "")
        author_handle = author_obj.get("handle", handle)
        web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}" if author_handle and rkey else ""

        lat, lon, matched = geocode_post(text)
        incident_date, is_recent = None, None
        if lat:
            incident_date, is_recent = match_to_incident(lat, lon, published)

        posts.append({
            "platform": "bluesky",
            "post_id": f"bsky_{did}_{rkey}",
            "author": author_handle,
            "content": text[:2000],
            "url": web_url,
            "published": published,
            "search_query": f"feed:{handle}",
            "subreddit": None,
            "score": post_obj.get("likeCount", 0),
            "num_comments": post_obj.get("replyCount", 0),
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


def ingest_bluesky():
    init_social_db()
    total_added = 0
    total_fetched = 0

    for handle in CHARLOTTE_ACCOUNTS:
        print(f"  Bluesky @{handle}...")
        did = resolve_handle(handle)
        if not did:
            print(f"    Could not resolve handle")
            continue
        sleep(REQUEST_DELAY)

        all_posts = []
        cursor = None
        for page in range(3):
            items, cursor = fetch_author_feed(did, limit=50, cursor=cursor)
            if not items:
                break
            crime_posts = process_feed_items(items, handle)
            all_posts.extend(crime_posts)
            print(f"    Page {page+1}: {len(items)} posts, {len(crime_posts)} crime-related")
            if not cursor:
                break
            sleep(REQUEST_DELAY)

        total_fetched += len(all_posts)
        saved = save_posts(all_posts)
        total_added += saved
        print(f"    Saved {saved} new posts")
        sleep(REQUEST_DELAY)

    session = SessionLocal()
    try:
        geocoded = session.query(SocialPost).filter(
            SocialPost.platform == "bluesky",
            SocialPost.latitude.isnot(None),
        ).count()
    finally:
        session.close()

    print(f"Bluesky done: {total_added} added, {geocoded} geocoded")
    return {"platform": "bluesky", "added": total_added, "fetched": total_fetched, "geocoded": geocoded}


if __name__ == "__main__":
    result = ingest_bluesky()
    print(result)
