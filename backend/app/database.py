"""SQLite database engine, session factory, and initialization."""
import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

# Ensure the data directory exists for the SQLite file.
os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},  # FastAPI runs across threads.
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Apply optional SQLite pragmas before SQLAlchemy creates tables."""
    journal_mode = settings.SQLITE_JOURNAL_MODE
    if journal_mode not in {"", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}:
        raise ValueError(f"Unsupported ADVISOR_SQLITE_JOURNAL_MODE: {journal_mode}")
    if journal_mode:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        finally:
            cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create legacy and V2 tables. Called once at application startup."""
    # Import all model modules so SQLAlchemy registers them on Base before create_all.
    from . import models, v2_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    _repair_historical_pnl_values()


def _apply_lightweight_migrations() -> None:
    """SQLite-friendly schema updates for the legacy no-Alembic tables.

    New V2 schema changes will move to Alembic. This compatibility block remains
    temporarily so existing advisor.db files continue to start safely.
    """
    inspector = inspect(engine)

    if inspector.has_table("runs"):
        run_columns = {c["name"] for c in inspector.get_columns("runs")}
        with engine.begin() as conn:
            if "transcript" not in run_columns:
                conn.execute(text("ALTER TABLE runs ADD COLUMN transcript TEXT"))
            if "sections" not in run_columns:
                conn.execute(text("ALTER TABLE runs ADD COLUMN sections JSON"))
            if "screenshot" not in run_columns:
                conn.execute(text("ALTER TABLE runs ADD COLUMN screenshot JSON"))

    if inspector.has_table("holdings_snapshots"):
        holding_columns = {c["name"] for c in inspector.get_columns("holdings_snapshots")}
        with engine.begin() as conn:
            if "pnl_amount" not in holding_columns:
                conn.execute(text("ALTER TABLE holdings_snapshots ADD COLUMN pnl_amount FLOAT"))

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


def _repair_historical_pnl_values() -> None:
    """Repair already stored amount-like PnL values. Safe to run repeatedly."""
    from . import models
    from .services.pnl import append_unique_corrections, normalize_pnl

    db = SessionLocal()
    try:
        touched_run_ids: set[int] = set()
        snapshots = (
            db.query(models.HoldingSnapshot)
            .filter(models.HoldingSnapshot.price.isnot(None))
            .filter(models.HoldingSnapshot.cost.isnot(None))
            .all()
        )
        for snapshot in snapshots:
            normalized_pnl, pnl_amount, correction = normalize_pnl(
                snapshot.code,
                snapshot.name,
                snapshot.pnl,
                snapshot.price,
                snapshot.cost,
                snapshot.pnl_amount,
            )
            if not correction:
                continue
            snapshot.pnl = normalized_pnl
            snapshot.pnl_amount = pnl_amount
            if snapshot.run:
                snapshot.run.evidence_pack = append_unique_corrections(
                    snapshot.run.evidence_pack,
                    [correction],
                )
                touched_run_ids.add(snapshot.run_id)
        if touched_run_ids:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()


def get_db():
    """FastAPI dependency: yield a per-request DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
