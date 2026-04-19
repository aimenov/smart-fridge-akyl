from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.database import init_db
from backend.app.logging_config import setup_logging
from backend.app.modules.capture_api import router as capture_router
from backend.app.modules.inventory_routes import router as inventory_router
from backend.app.modules.scheduler import start_scheduler

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"

logger = logging.getLogger(__name__)
trace_log = logging.getLogger("smart_fridge.http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(
        settings.log_level,
        json_logs=settings.json_logs,
        http_trace=settings.http_trace,
    )
    logger.info(
        "startup log_level=%s scheduler_enabled=%s database=%s",
        settings.log_level,
        settings.scheduler_enabled,
        settings.database_url.split("///")[-1][:80],
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
    # Avoid HTTP/1.1 keep-alive reuse for API calls — some clients + httptools trip over the
    # next request on the same socket (uvicorn logs "Invalid HTTP request received").
    if request.url.path.startswith("/api"):
        response.headers["connection"] = "close"
    return response


@app.middleware("http")
async def http_request_trace(request: Request, call_next):
    """
    Logs every request that reaches ASGI (never runs if uvicorn rejects bytes first).
    Registered after security_headers so this runs first on inbound traffic.
    """
    path = request.url.path
    static_ok = settings.http_trace_static or path.startswith("/api") or path == "/health"
    if not settings.http_trace:
        return await call_next(request)
    if not static_ok:
        return await call_next(request)

    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    client = request.client.host if request.client else "?"
    port = request.client.port if request.client else 0
    clen = request.headers.get("content-length")
    ctype = request.headers.get("content-type")
    ua = (request.headers.get("user-agent") or "")[:160]
    trace_log.info(
        "request_begin rid=%s %s %s%s client=%s:%s scheme=%s clen=%s ctype=%s ua=%s",
        rid,
        request.method,
        path,
        f"?{request.url.query}" if request.url.query else "",
        client,
        port,
        request.url.scheme,
        clen,
        (ctype or "")[:120],
        ua,
    )
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        trace_log.exception(
            "request_failed rid=%s %s %s client=%s",
            rid,
            request.method,
            path,
            client,
        )
        raise
    ms = (time.perf_counter() - t0) * 1000.0
    trace_log.info(
        "request_end rid=%s %s %s status=%s ms=%.1f",
        rid,
        request.method,
        path,
        getattr(response, "status_code", "?"),
        ms,
    )
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(RequestValidationError)
async def validation_log_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 often means multipart field name mismatch; log for phone upload debugging."""
    trace_log.warning(
        "validation_422 path=%s client=%s ctype=%s clen=%s errors=%s",
        request.url.path,
        request.client.host if request.client else None,
        (request.headers.get("content-type") or "")[:200],
        request.headers.get("content-length"),
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app.include_router(capture_router)
app.include_router(inventory_router)


@app.get("/health")
def health():
    return {"status": "ok"}


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
