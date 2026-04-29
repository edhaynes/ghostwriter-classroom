"""Entry point for the Ghostwriter Classroom service.

Run locally:
    uvicorn main:app --reload --port 8081

In OpenShift: deployed as a second Service in the ghostwriter namespace,
exposed via its own Route at e.g. classroom-ghostwriter.apps.<cluster>.
"""

import asyncio
import logging
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from classroom.api import router as classroom_router
from classroom.idle_monitor import idle_monitor_task, record_activity, get_idle_seconds

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ghostwriter Classroom",
    description="Live classroom demo — crowd story arcs, AI grading, human vs. machine judging.",
    version="0.5.5",
)


# Middleware to track activity
@app.middleware("http")
async def activity_tracker(request: Request, call_next):
    # Record activity for any non-health endpoint
    if not request.url.path.startswith("/health"):
        record_activity()
    response = await call_next(request)
    return response


# Startup event to launch idle monitor
@app.on_event("startup")
async def startup_event():
    if os.getenv("ENABLE_IDLE_SHUTDOWN"):
        logger.info("Starting idle monitor task")
        asyncio.create_task(idle_monitor_task())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/idle-status")
async def idle_status():
    """Check idle time and shutdown status."""
    from classroom.idle_monitor import IDLE_TIMEOUT
    idle_seconds = get_idle_seconds()
    enabled = bool(os.getenv("ENABLE_IDLE_SHUTDOWN"))
    return {
        "idle_shutdown_enabled": enabled,
        "idle_seconds": idle_seconds,
        "timeout_seconds": IDLE_TIMEOUT,
        "remaining_seconds": max(0, IDLE_TIMEOUT - idle_seconds) if enabled else None,
        "will_shutdown_in": f"{max(0, IDLE_TIMEOUT - idle_seconds) // 60} minutes" if enabled else "disabled"
    }


@app.post("/extend-session")
async def extend_session():
    """Extend the idle timeout by resetting activity timer."""
    record_activity()
    from classroom.idle_monitor import IDLE_TIMEOUT
    return {
        "ok": True,
        "message": "Session extended",
        "timeout_seconds": IDLE_TIMEOUT,
        "timeout_minutes": IDLE_TIMEOUT // 60
    }


# Include classroom API routes
app.include_router(classroom_router)

# Serve the single-page frontend (MUST be last - catches all remaining routes)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
