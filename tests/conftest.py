from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from news_aggregator.db.models import Base
from news_aggregator.db.session import make_session_factory


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def session(session_factory):
    with session_factory() as s:
        yield s
