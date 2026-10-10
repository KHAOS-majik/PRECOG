from sqlalchemy import Column, String, Float, DateTime, Integer, Text, Index
from app.models.incident import Base, engine


class SocialPost(Base):
    __tablename__ = "social_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String, nullable=False)
    post_id = Column(String, unique=True, nullable=False)
    author = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    url = Column(String, nullable=True)
    published = Column(DateTime, nullable=True)
    search_query = Column(String, nullable=True)
    subreddit = Column(String, nullable=True)
    score = Column(Integer, nullable=True)
    num_comments = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    matched_address = Column(String, nullable=True)
    matched_incident_date = Column(DateTime, nullable=True)
    is_recent_crime = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_social_published", "published"),
        Index("ix_social_platform", "platform"),
        Index("ix_social_lat_lon", "latitude", "longitude"),
    )


def init_social_db():
    Base.metadata.create_all(bind=engine)
