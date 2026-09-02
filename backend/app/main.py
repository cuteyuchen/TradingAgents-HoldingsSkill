"""FastAPI application entry point."""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from . import auth
from .config import settings
from .database import SessionLocal, init_db
from .services import analysis_engine
from .services.market_identity_sync import (
    calendar_status,
    initialize_local_market_identity,
    start_remote_market_identity_sync,
    stop_remote_market_identity_sync,
)
from .services.market_snapshot_service import (
    hydrate_runtime_provider_health,
    sync_runtime_provider_health,
)
from .services.scheduler import start_scheduler, stop_scheduler
from .services.realtime_monitor import start_realtime_monitor, stop_realtime_monitor
from .services.skill_runtime import runtime_metadata, runtime_prompt
from .system.backup import BackupError, ensure_pre_upgrade_backup
from .system.health import liveness, readiness
from .system.logging import RequestIDMiddleware, configure_logging
from .system.startup import run_startup_preflight
from .system.workers import signal_workers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("advisor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage, the versioned Skill runtime, and the scheduler."""
    configure_logging()
    try:
        ensure_pre_upgrade_backup()
    except BackupError as exc:
        logger.error("Pre-upgrade backup guard failed: %s", exc)
        raise RuntimeError("PRE_UPGRADE_BACKUP_FAILED") from exc
    init_db()
    with SessionLocal() as db:
        preflight = run_startup_preflight(db)
    if preflight["blocked"]:
        blocked = ", ".join(
            f"{key}={value.get('status')}" for key, value in sorted(preflight["checks"].items())
        )
        raise RuntimeError(f"STARTUP_PREFLIGHT_BLOCKED:{blocked}")
    with SessionLocal() as db:
        from .history.sync import reclaim_stale_history_sync_runs

        stale_syncs = reclaim_stale_history_sync_runs(db)
        if stale_syncs:
            db.commit()
            logger.info("Reclaimed %s stale historical sync runs", len(stale_syncs))
    # Prevent a process restart from forgetting an already-open provider
    # circuit while the durable health table still reports it as blocked.
    with SessionLocal() as db:
        restored_provider_health = hydrate_runtime_provider_health(db)
        sync_runtime_provider_health(db)
        db.commit()
    if restored_provider_health:
        logger.info("Restored %s provider health states", len(restored_provider_health))
    token = auth.ensure_token()
    if token:
        logger.info("Legacy ADVISOR_TOKEN configured")
    if settings.APP_SECRET_KEY == "dev-only-change-me":
        logger.warning("APP_SECRET_KEY uses the development default; set a stable secret in production.")

    # Populate the scheduler's local calendar before it starts.  This path is
    # fully offline; optional provider refreshes run later in a daemon thread.
    identity_status = initialize_local_market_identity()
    if identity_status["status"] != "ready":
        logger.warning("Market calendar is not ready: %s", identity_status["status"])

    # The runtime prompt and its hash come from skill/tradingagents-holdings-advisor,
    # making the repository Skill the audited source of analysis rules.
    analysis_engine.CORE_RULES = runtime_prompt()
    skill = runtime_metadata()
    logger.info(
        "Loaded holdings Skill %s v%s (%s)",
        skill["name"],
        skill["version"],
        str(skill["runtime_sha256"])[:12],
    )

    start_scheduler()
    start_remote_market_identity_sync()
    start_realtime_monitor()
    try:
        yield
    finally:
        stop_scheduler()
        signal_workers(timeout=5.0)
        time.sleep(0.5)
        stop_realtime_monitor()
        stop_remote_market_identity_sync()


app = FastAPI(
    title="TradingAgents Holdings Advisor API",
    description=(
        "V1 archive compatibility plus V2 authentication, model configuration, "
        "portfolio screenshot parsing, analysis jobs, reports, schedules, and notifications."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)

from .routers import archives  # noqa: E402

app.include_router(archives.router)

from .routers import (  # noqa: E402
    analysis_v2,
    auth_v2,
    automation_v2,
    candidates_v3,
    governance_v3,
    history_v3,
    market_v3,
    model_health_v2,
    model_settings_v2,
    portfolios_v2,
    market_engine_v3,
    memory_v3,
    system_v3,
    research_v3,
    monitor_v3,
    operations_v3,
    portfolio_v3,
    shadow_v3,
    triggers_v3,
    fuyao_v3,
)

app.include_router(auth_v2.router)
app.include_router(model_settings_v2.router)
app.include_router(model_health_v2.router)
app.include_router(portfolios_v2.router)
app.include_router(analysis_v2.router)
app.include_router(automation_v2.router)
app.include_router(candidates_v3.router)
app.include_router(governance_v3.router)
app.include_router(history_v3.router)
app.include_router(market_v3.router)
app.include_router(market_engine_v3.router)
app.include_router(memory_v3.router)
app.include_router(research_v3.router)
app.include_router(monitor_v3.router)
app.include_router(operations_v3.router)
app.include_router(triggers_v3.router)
app.include_router(portfolio_v3.router)
app.include_router(shadow_v3.router)
app.include_router(fuyao_v3.router)
app.include_router(system_v3.router)


@app.get("/healthz")
def healthz() -> dict:
    return liveness()


@app.get("/healthz/live")
def healthz_live() -> dict:
    return liveness()


@app.get("/healthz/ready")
def healthz_ready() -> JSONResponse:
    result = readiness()
    return JSONResponse(
        content=result,
        status_code=200 if result["ready"] else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.get("/api/v1/auth/verify")
def verify_auth(_: str = Depends(auth.require_token)) -> dict:
    return {"status": "ok"}


STATIC_DIR = Path(settings.STATIC_DIR).resolve()


@app.get("/{frontend_path:path}", include_in_schema=False)
def serve_frontend(frontend_path: str, request: Request):
    """Serve the bundled Vue application without masking missing API routes."""
    if not STATIC_DIR.is_dir() or frontend_path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    requested_path = (STATIC_DIR / frontend_path).resolve()
    try:
        requested_path.relative_to(STATIC_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    if requested_path.is_file():
        return FileResponse(requested_path)

    accepts_html = frontend_path == "" or "text/html" in request.headers.get("accept", "")
    if not accepts_html or Path(frontend_path).suffix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(index_path)
