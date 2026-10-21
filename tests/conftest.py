import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, make_engine, make_session_factory
from app.models import Unit
from app.services.units import ensure_default_units


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        ensure_default_units(s)
        s.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def units(db):
    return {u.code: u for u in db.scalars(select(Unit))}


@pytest.fixture
def client(session_factory):
    from app.main import create_app

    application = create_app(session_factory, background_tasks=False, secret_key="test-secret")
    with TestClient(application) as c:
        yield c
