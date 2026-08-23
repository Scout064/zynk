from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import about, auth, configs, devices, schedules, status
from app.core.config import get_settings
from app.core.version import APP_NAME, __version__
from app.db.base import get_engine, init_db, session_scope
from app.scheduler import jobs
from app.services import gitstore
from app.services import status as status_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("zynk")

_status_task: asyncio.Task | None = None


async def _status_loop() -> None:
    settings = get_settings()
    while True:
        try:
            db = session_scope()
            try:
                await status_service.poll_all(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("status poll failed")
        await asyncio.sleep(settings.status_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _status_task
    app.state.started_at = datetime.now(UTC)
    settings = get_settings()
    get_engine()
    init_db()
    gitstore.ensure_repo()

    from app.api.deps import bootstrap_admin

    db = session_scope()
    try:
        bootstrap_admin(db)
        jobs.sync_jobs(db)
    finally:
        db.close()

    jobs.start_scheduler()
    _status_task = asyncio.create_task(_status_loop())
    log.info("Zynk started (data dir: %s)", settings.data_dir)
    yield
    if _status_task is not None:
        _status_task.cancel()
    jobs.shutdown_scheduler()
    log.info("Zynk stopped")


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(about.router)
    app.include_router(auth.router)
    app.include_router(devices.router)
    app.include_router(configs.router)
    app.include_router(schedules.router)
    app.include_router(status.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
