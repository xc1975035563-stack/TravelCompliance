from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import DATABASE_URL
from app.models import Base

def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False, "timeout": 30},
        }
    return {}


engine = create_engine(DATABASE_URL, future=True, **_engine_kwargs(DATABASE_URL))


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _):
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=8000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(engine)


def session_for(url: str):
    other = create_engine(url, future=True, **_engine_kwargs(url))

    @event.listens_for(other, "connect")
    def _pragmas(dbapi_connection, _):
        if url.startswith("sqlite"):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=8000")
            cursor.close()

    Base.metadata.create_all(other)
    return sessionmaker(bind=other, autoflush=False, autocommit=False, expire_on_commit=False), other
