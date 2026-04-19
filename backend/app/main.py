from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.database import init_db
from backend.app.logging_config import setup_logging
from backend.app.modules.capture_api import router as capture_router
from backend.app.modules.inventory_routes import router as inventory_router
from backend.app.modules.scheduler import start_scheduler

logger = logging.getLogger(__name__)


def _resolve_web_dir() -> Path | None:
    """Find ``web/`` with ``index.html`` — editable installs use repo layout; wheels need ``SMART_FRIDGE_WEB_ROOT``."""
    if settings.web_root is not None:
        p = Path(settings.web_root).expanduser().resolve()
        if p.is_dir() and (p / "index.html").is_file():
            return p
        logger.warning("SMART_FRIDGE_WEB_ROOT is set but missing index.html: %s", p)
    here = Path(__file__).resolve().parent
    for base in [here, *here.parents]:
        cand = base / "web"
        if cand.is_dir() and (cand / "index.html").is_file():
            return cand
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level, json_logs=settings.json_logs)
    wd = _resolve_web_dir()
    logger.info(
        "startup log_level=%s scheduler_enabled=%s database=%s web_ui=%s",
        settings.log_level,
        settings.scheduler_enabled,
        settings.database_url.split("///")[-1][:80],
        str(wd) if wd else "(not mounted — set SMART_FRIDGE_WEB_ROOT or run from repo clone)",
    )
    init_db()
    sched = None
    if settings.scheduler_enabled:
        sched = start_scheduler()
        logger.info("background scheduler started")
    yield
    if sched is not None:
        sched.shutdown(wait=False)
        logger.info("scheduler stopped")


app = FastAPI(title="Smart Fridge", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Allow camera on same origin; mobile browsers require secure context + explicit policy."""
    response = await call_next(request)
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
    return response


app.include_router(capture_router)
app.include_router(inventory_router)


@app.get("/health")
def health():
    return {"status": "ok"}


_WEB_DIR = _resolve_web_dir()
if _WEB_DIR is not None:
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
else:
    logger.warning(
        "Static web UI not found — open only /api/* and /health. "
        "Clone the repo (see `web/`) or set SMART_FRIDGE_WEB_ROOT to that folder.",
    )
