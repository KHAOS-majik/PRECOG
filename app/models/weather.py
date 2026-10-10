from sqlalchemy import Column, String, Float, Date, Integer, Index
from app.models.incident import Base, engine


class DailyWeather(Base):
    __tablename__ = "daily_weather"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False, index=True)
    temp_max = Column(Float, nullable=True)
    temp_min = Column(Float, nullable=True)
    temp_mean = Column(Float, nullable=True)
    precipitation = Column(Float, nullable=True)
    rain = Column(Float, nullable=True)
    snowfall = Column(Float, nullable=True)
    wind_speed_max = Column(Float, nullable=True)
    daylight_hours = Column(Float, nullable=True)


def init_weather_db():
    Base.metadata.create_all(bind=engine)
