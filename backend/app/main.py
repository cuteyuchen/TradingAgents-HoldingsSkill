"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .config import settings
from .database import SessionLocal, init_db
from .services import benchmark_fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("advisor")


def _refresh_benchmark_job() -> None:
    db = SessionLocal()
    try:
        benchmark_fetcher.refresh_today(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduled benchmark refresh failed: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, ensure token, backfill benchmark, schedule daily refresh."""
    init_db()
    token = auth.ensure_token()
    logger.info("ADVISOR_TOKEN in use: %s", token[:12] + "...")

    if settings.BENCHMARK_FETCH_ON_START:
        db = SessionLocal()
        try:
            benchmark_fetcher.backfill(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("startup benchmark backfill failed: %s", exc)
        finally:
            db.close()

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(_refresh_benchmark_job, "cron", hour=15, minute=35, day_of_week="mon-fri")
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Daily Holdings Trading Advisor — Persistence API",
    description="Stores skill runs (decisions, debates, holdings, candidates) and computes alpha vs CSI 300.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers.
from .routers import (  # noqa: E402
    archives, benchmark, candidates, health, holdings, memory, portfolio, runs, watchlist,
)

app.include_router(archives.router)
app.include_router(runs.router)
app.include_router(portfolio.router)
app.include_router(holdings.router)
app.include_router(candidates.router)
app.include_router(benchmark.router)
app.include_router(watchlist.router)
app.include_router(health.router)
app.include_router(memory.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/auth/verify")
def verify_auth(_: str = Depends(auth.require_token)) -> dict:
    return {"status": "ok"}
