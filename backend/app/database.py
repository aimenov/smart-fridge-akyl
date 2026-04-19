import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config import settings
from backend.app.models.entities import Base

logger = logging.getLogger(__name__)


def _ensure_sqlite_parent_dir(url: str) -> None:
    if ":memory:" in url:
        return
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        raw = url.removeprefix("sqlite:///")
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_dir(settings.database_url)

connect_args: dict = {}
engine_kwargs: dict = {"echo": False, "connect_args": connect_args}

if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
if ":memory:" in settings.database_url:
    engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **engine_kwargs)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    settings.scan_storage.mkdir(parents=True, exist_ok=True)
    settings.uploads_storage.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    logger.debug("database tables ensured (engine=%s)", settings.database_url.split("@")[-1])
