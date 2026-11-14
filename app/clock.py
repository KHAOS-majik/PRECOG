"""Reference time for every analysis window.

Windows anchor on the newest incident in the database rather than the wall
clock, so a publication lag never reads as a drop in crime. Inside `as_of`,
queries also cannot see anything dated on or after the given moment, which is
what lets past predictions be reconstructed without leaking the outcome.
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta

from sqlalchemy import event, func
from sqlalchemy.orm import Session, with_loader_criteria

from app.models.incident import Incident, SessionLocal
from app.models.service_request import ServiceRequest
from app.models.weather import DailyWeather

_as_of = ContextVar("precog_as_of", default=None)
_latest = {"value": None, "ts": 0.0}
_LATEST_TTL = 300


def current_as_of():
    return _as_of.get()


def reset_data_through():
    _latest["ts"] = 0.0


def data_through():
    """Date of the newest incident on record, or None when the table is empty."""
    now = time.time()
    if _latest["value"] is None or now - _latest["ts"] > _LATEST_TTL:
        session = SessionLocal()
        try:
            latest = session.query(func.max(Incident.date_reported)).scalar()
        finally:
            session.close()
        _latest["value"] = latest.date() if latest else None
        _latest["ts"] = now
    return _latest["value"]


def ref_now():
    pinned = _as_of.get()
    if pinned is not None:
        return pinned
    latest = data_through()
    if latest is None:
        return datetime.utcnow()
    anchor = datetime.combine(latest, datetime.min.time()) + timedelta(days=1)
    return min(datetime.utcnow(), anchor)


@contextmanager
def as_of(moment):
    token = _as_of.set(moment)
    try:
        yield
    finally:
        _as_of.reset(token)


@event.listens_for(Session, "do_orm_execute")
def _hide_the_future(state):
    moment = _as_of.get()
    if moment is None or not state.is_select:
        return
    state.statement = state.statement.options(
        with_loader_criteria(Incident, Incident.date_reported < moment, include_aliases=True),
        with_loader_criteria(ServiceRequest, ServiceRequest.date_created < moment, include_aliases=True),
        with_loader_criteria(DailyWeather, DailyWeather.date < moment.date(), include_aliases=True),
    )
