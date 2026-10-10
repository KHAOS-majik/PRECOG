import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.incident import engine, SessionLocal, Incident
from app.models.news import NewsArticle, init_news_db

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

SEARCH_QUERIES = [
    "Charlotte NC crime",
    "Charlotte NC shooting",
    "Charlotte NC homicide",
    "Charlotte NC robbery",
    "Charlotte NC assault",
    "CMPD arrest",
    "CMPD investigation",
    "Charlotte NC stabbing",
    "Charlotte NC carjacking",
]

CLT_STREETS = [
    "Tryon", "Trade", "Independence", "Freedom", "Beatties Ford",
    "Central Ave", "South Blvd", "Nations Ford", "Albemarle",
    "Statesville", "Graham", "Brookshire", "Wilkinson", "Sugar Creek",
    "Eastway", "The Plaza", "Monroe", "Providence", "Park",
    "Morehead", "Clanton", "West Blvd", "Remount", "Tuckaseegee",
    "Mt Holly", "Rozelles Ferry", "Billingsley", "Rozzelles Ferry",
]

CLT_NEIGHBORHOODS = {
    "Uptown": (35.2271, -80.8431),
    "NoDa": (35.2470, -80.8176),
    "Plaza Midwood": (35.2190, -80.8090),
    "South End": (35.2108, -80.8565),
    "Dilworth": (35.2050, -80.8480),
    "Myers Park": (35.1880, -80.8300),
    "Eastland": (35.2020, -80.7600),
    "University City": (35.3080, -80.7430),
    "Ballantyne": (35.0570, -80.8480),
    "Steele Creek": (35.1030, -80.9470),
    "Northlake": (35.3440, -80.8860),
    "Mint Hill": (35.1790, -80.6470),
    "Matthews": (35.1168, -80.7237),
    "Pineville": (35.0832, -80.8512),
    "West Charlotte": (35.2300, -80.8900),
    "Hidden Valley": (35.2740, -80.7970),
    "Grier Heights": (35.1950, -80.8180),
    "Enderly Park": (35.2170, -80.8800),
    "Belmont": (35.2280, -80.8240),
    "Optimist Park": (35.2340, -80.8230),
    "Wesley Heights": (35.2260, -80.8660),
    "Seversville": (35.2310, -80.8610),
    "Cherry": (35.2060, -80.8400),
    "Lockwood": (35.2350, -80.8120),
    "Camp Greene": (35.2230, -80.8840),
}


def fetch_google_news(query, num=50):
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en", "num": num}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Precog/1.0)"}
    resp = requests.get(GOOGLE_NEWS_RSS, params=params, headers=headers, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    articles = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source = item.findtext("source", "")
        desc = unescape(item.findtext("description", ""))
        desc_clean = re.sub(r"<[^>]+>", "", desc).strip()

        pub_dt = None
        if pub_date:
            try:
                pub_dt = parsedate_to_datetime(pub_date)
            except Exception:
                pass

        articles.append({
            "title": title,
            "url": link,
            "source": source,
            "published": pub_dt,
            "snippet": desc_clean[:500] if desc_clean else None,
            "search_query": query,
        })

    return articles


def geocode_article(article):
    text = f"{article['title']} {article.get('snippet', '')}"

    for name, (lat, lon) in CLT_NEIGHBORHOODS.items():
        if name.lower() in text.lower():
            article["latitude"] = lat
            article["longitude"] = lon
            article["matched_address"] = name
            return article

    for street in CLT_STREETS:
        pattern = re.compile(
            rf"\b\d{{2,5}}\s+(?:\w+\s+)?{re.escape(street)}\b", re.IGNORECASE
        )
        match = pattern.search(text)
        if match:
            article["matched_address"] = match.group(0)
            session = SessionLocal()
            try:
                incident = (
                    session.query(Incident.latitude, Incident.longitude)
                    .filter(Incident.address.ilike(f"%{street}%"))
                    .filter(Incident.latitude.isnot(None))
                    .first()
                )
                if incident:
                    article["latitude"] = incident.latitude
                    article["longitude"] = incident.longitude
            finally:
                session.close()
            return article

    block_pattern = re.compile(
        r"\b(\d{2,5})\s+block\s+of\s+([\w\s]+?)(?:Road|Street|Avenue|Boulevard|Drive|Lane|Way|Blvd|St|Ave|Rd|Dr|Ln)\b",
        re.IGNORECASE
    )
    match = block_pattern.search(text)
    if match:
        street_name = match.group(2).strip()
        article["matched_address"] = match.group(0)
        session = SessionLocal()
        try:
            incident = (
                session.query(Incident.latitude, Incident.longitude)
                .filter(Incident.address.ilike(f"%{street_name}%"))
                .filter(Incident.latitude.isnot(None))
                .first()
            )
            if incident:
                article["latitude"] = incident.latitude
                article["longitude"] = incident.longitude
        finally:
            session.close()
        return article

    return article


def match_to_incident(article):
    if not article.get("latitude") or not article.get("published"):
        return article
    session = SessionLocal()
    try:
        pub = article["published"]
        lat, lon = article["latitude"], article["longitude"]
        lookback_90 = pub - timedelta(days=90)
        nearby = (
            session.query(Incident.date_reported)
            .filter(
                Incident.latitude.between(lat - 0.005, lat + 0.005),
                Incident.longitude.between(lon - 0.005, lon + 0.005),
                Incident.date_reported >= lookback_90,
                Incident.date_reported <= pub + timedelta(days=1),
            )
            .order_by(Incident.date_reported.desc())
            .first()
        )
        if nearby:
            article["matched_incident_date"] = nearby.date_reported
            days_old = (pub - nearby.date_reported).days
            article["is_recent_crime"] = 1 if days_old <= 30 else 0
        else:
            article["is_recent_crime"] = 0
    finally:
        session.close()
    return article


def save_articles(articles):
    if not articles:
        return 0
    records = []
    for a in articles:
        records.append({
            "title": a["title"],
            "url": a["url"],
            "source": a.get("source"),
            "published": a.get("published"),
            "snippet": a.get("snippet"),
            "search_query": a.get("search_query"),
            "latitude": a.get("latitude"),
            "longitude": a.get("longitude"),
            "matched_address": a.get("matched_address"),
            "matched_npa": a.get("matched_npa"),
            "matched_incident_date": a.get("matched_incident_date"),
            "is_recent_crime": a.get("is_recent_crime"),
        })
    with engine.begin() as conn:
        stmt = sqlite_insert(NewsArticle).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
        result = conn.execute(stmt)
        return result.rowcount


def ingest_news():
    init_news_db()
    total_added = 0
    total_fetched = 0

    for query in SEARCH_QUERIES:
        print(f"  Searching: {query}")
        try:
            articles = fetch_google_news(query)
            total_fetched += len(articles)
            for article in articles:
                geocode_article(article)
                match_to_incident(article)
            saved = save_articles(articles)
            total_added += saved
            print(f"    Found {len(articles)}, saved {saved} new")
        except Exception as e:
            print(f"    Error: {e}")

    geocoded = 0
    session = SessionLocal()
    try:
        geocoded = session.query(NewsArticle).filter(
            NewsArticle.latitude.isnot(None)
        ).count()
    finally:
        session.close()

    print(f"Done: {total_added} articles added, {geocoded} geocoded")
    return {"added": total_added, "fetched": total_fetched, "geocoded": geocoded}


if __name__ == "__main__":
    result = ingest_news()
    print(result)
