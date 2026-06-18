"""SQLite database engine, session factory, and initialization."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

# Ensure the data directory exists for the SQLite file.
os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},  # FastAPI runs across threads.
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create all tables. Called once at application startup."""
    # Import models so SQLAlchemy registers them on Base before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yield a per-request DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
