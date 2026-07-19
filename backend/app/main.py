"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .config import settings
from .database import init_db
from .services import analysis_engine
from .services.scheduler import start_scheduler, stop_scheduler
from .services.skill_runtime import runtime_metadata, runtime_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("advisor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage, the versioned Skill runtime, and the scheduler."""
    init_db()
    token = auth.ensure_token()
    logger.info("Legacy ADVISOR_TOKEN in use: %s", token[:12] + "...")
    if settings.APP_SECRET_KEY == "dev-only-change-me":
        logger.warning("APP_SECRET_KEY uses the development default; set a stable secret in production.")

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
    try:
        yield
    finally:
        stop_scheduler()


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

from .routers import archives  # noqa: E402

app.include_router(archives.router)

from .routers import (  # noqa: E402
    analysis_v2,
    auth_v2,
    automation_v2,
    model_health_v2,
    model_settings_v2,
    portfolios_v2,
)

app.include_router(auth_v2.router)
app.include_router(model_settings_v2.router)
app.include_router(model_health_v2.router)
app.include_router(portfolios_v2.router)
app.include_router(analysis_v2.router)
app.include_router(automation_v2.router)


@app.get("/healthz")
def healthz() -> dict:
    skill = runtime_metadata()
    return {
        "status": "ok",
        "version": app.version,
        "scheduler": settings.SCHEDULER_ENABLED,
        "skill_version": skill["version"],
        "skill_runtime_sha256": skill["runtime_sha256"],
    }


@app.get("/api/v1/auth/verify")
def verify_auth(_: str = Depends(auth.require_token)) -> dict:
    return {"status": "ok"}
