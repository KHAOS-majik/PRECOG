from sqlalchemy import Column, Date, DateTime, Integer, String, Text

from app.models.incident import Base, engine


class AccuracyWeek(Base):
    """One week of predictions and, once the week has been published, how they held up."""
    __tablename__ = "accuracy_weeks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_start = Column(Date, unique=True, nullable=False, index=True)
    # "live": saved before the week began. "reconstructed": rebuilt later from data dated before the week.
    mode = Column(String, nullable=False)
    data_through = Column(Date, nullable=False)
    created_at = Column(DateTime, nullable=False)
    predictions = Column(Text, nullable=False)
    results = Column(Text, nullable=True)
    scored_at = Column(DateTime, nullable=True)


def init_accuracy_db():
    Base.metadata.create_all(bind=engine)
