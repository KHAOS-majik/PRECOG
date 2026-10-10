from sqlalchemy import Column, String, Float, DateTime, Integer, Text, Index
from app.models.incident import Base, engine

class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    url = Column(String, unique=True, nullable=False)
    source = Column(String, nullable=True)
    published = Column(DateTime, nullable=True)
    snippet = Column(Text, nullable=True)
    search_query = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    matched_address = Column(String, nullable=True)
    matched_npa = Column(String, nullable=True)
    matched_incident_date = Column(DateTime, nullable=True)
    is_recent_crime = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_news_published", "published"),
        Index("ix_news_lat_lon", "latitude", "longitude"),
    )

def init_news_db():
    Base.metadata.create_all(bind=engine)
