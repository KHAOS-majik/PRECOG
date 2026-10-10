from sqlalchemy import Column, String, Float, DateTime, Integer, Text, Index
from app.models.incident import Base, engine


class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_number = Column(String, unique=True, nullable=False, index=True)
    case_type = Column(String, nullable=True)
    full_address = Column(String, nullable=True)
    case_origin = Column(String, nullable=True)
    npa = Column(String, nullable=True)
    council_district = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    case_status = Column(String, nullable=True)
    conclusion = Column(String, nullable=True)
    date_created = Column(DateTime, nullable=True)
    date_closed = Column(DateTime, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_sr_date_created", "date_created"),
        Index("ix_sr_case_type", "case_type"),
        Index("ix_sr_npa", "npa"),
        Index("ix_sr_lat_lon", "latitude", "longitude"),
    )


def init_sr_db():
    Base.metadata.create_all(bind=engine)
