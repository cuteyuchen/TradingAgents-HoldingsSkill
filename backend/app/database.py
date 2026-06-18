"""SQLite database engine, session factory, and initialization."""
import os

from sqlalchemy import create_engine, inspect, text
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
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """SQLite-friendly schema updates for the no-Alembic single-file setup."""
    inspector = inspect(engine)

    if inspector.has_table("runs"):
        run_columns = {c["name"] for c in inspector.get_columns("runs")}
        with engine.begin() as conn:
            if "transcript" not in run_columns:
                conn.execute(text("ALTER TABLE runs ADD COLUMN transcript TEXT"))
            if "sections" not in run_columns:
                conn.execute(text("ALTER TABLE runs ADD COLUMN sections JSON"))

    if inspector.has_table("health_log"):
        health_columns = {c["name"] for c in inspector.get_columns("health_log")}
        with engine.begin() as conn:
            if "code" not in health_columns:
                conn.execute(text("ALTER TABLE health_log ADD COLUMN code VARCHAR(16)"))

            sql = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='health_log'"
            )).scalar() or ""
            if "uq_health_checkpoint" in sql or "UNIQUE (checkpoint)" in sql:
                conn.execute(text("""
                    CREATE TABLE health_log_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code VARCHAR(16),
                        checkpoint VARCHAR(16) NOT NULL,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        last_failure_at DATETIME,
                        last_success_at DATETIME,
                        note TEXT
                    )
                """))
                conn.execute(text("""
                    INSERT INTO health_log_new (
                        id, code, checkpoint, consecutive_failures,
                        last_failure_at, last_success_at, note
                    )
                    SELECT id, code, checkpoint, consecutive_failures,
                           last_failure_at, last_success_at, note
                    FROM health_log
                """))
                conn.execute(text("DROP TABLE health_log"))
                conn.execute(text("ALTER TABLE health_log_new RENAME TO health_log"))
                conn.execute(text("CREATE INDEX ix_health_log_code ON health_log (code)"))
                conn.execute(text("CREATE INDEX ix_health_log_checkpoint ON health_log (checkpoint)"))


def get_db():
    """FastAPI dependency: yield a per-request DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
