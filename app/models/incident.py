from sqlalchemy import Column, String, Float, DateTime, Integer, Index, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./precog.db")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_report_id = Column(String, unique=True, nullable=False, index=True)
    date_reported = Column(DateTime, nullable=True)
    date_incident_began = Column(DateTime, nullable=True)
    date_incident_end = Column(DateTime, nullable=True)
    clearance_date = Column(DateTime, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    nibrs_code = Column(String, nullable=True)
    nibrs_code_group = Column(String, nullable=True)
    nibrs_offense_description = Column(String, nullable=True)
    clearance_status = Column(String, nullable=True)
    clearance_detail_status = Column(String, nullable=True)
    division_id = Column(String, nullable=True)
    npa = Column(String, nullable=True)
    place_type_description = Column(String, nullable=True)
    cmpd_patrol_division = Column(String, nullable=True)
    location_type = Column(String, nullable=True)
    place_detail = Column(String, nullable=True)
    __table_args__ = (
        Index("ix_lat_lon", "latitude", "longitude"),
        Index("ix_date_reported", "date_reported"),
        Index("ix_nibrs_code", "nibrs_code"),
        Index("ix_npa", "npa"),
        Index("ix_division_id", "division_id"),
        Index("ix_address", "address"),
        Index("ix_date_nibrs", "date_reported", "nibrs_code"),
    )

def init_db():
    Base.metadata.create_all(bind=engine)
