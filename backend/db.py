from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

from backend.config import settings


def now():
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    cfg = settings()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    sqlite = cfg.database_url.startswith("sqlite")
    result = create_engine(
        cfg.database_url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
    )
    if sqlite:

        @event.listens_for(result, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return result


@contextmanager
def session_scope():
    with Session(engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_db():
    with session_scope() as session:
        yield session
