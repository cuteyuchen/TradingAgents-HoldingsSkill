"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .config import settings
from .database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("advisor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB and ensure the access token exists."""
    init_db()
    token = auth.ensure_token()
    logger.info("ADVISOR_TOKEN in use: %s", token[:12] + "...")
    yield


app = FastAPI(
    title="Daily Holdings Trading Advisor Archive API",
    description="Stores completed advice archives with screenshot, parsed holdings JSON, and advice Markdown.",
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

# /*********************** 归档接口注册 *********************/
from .routers import archives  # noqa: E402

app.include_router(archives.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/auth/verify")
def verify_auth(_: str = Depends(auth.require_token)) -> dict:
    return {"status": "ok"}
