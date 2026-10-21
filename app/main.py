"""FastAPI აპლიკაციის აწყობა."""
import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.db import get_session_factory
from app.i18n import t
from app.services import backup
from app.web.routes import api, dispatch, ingredients, inventory, production, products, receipts, recipes, reports, system
from app.web.templating import render

log = logging.getLogger(__name__)


async def _periodic_backup():
    """ყოველ 24 საათში ერთხელ — ბაზის ასლი (თუ ბოლო ასლი უფრო ძველია)."""
    db_path = backup.sqlite_path_from_url(config.DATABASE_URL)
    if db_path is None:
        return
    while True:
        await asyncio.sleep(3600)
        last = backup.last_backup_time(config.BACKUP_DIR)
        if last is None or datetime.now() - last >= timedelta(hours=config.BACKUP_INTERVAL_HOURS):
            try:
                path = backup.create_backup(db_path, config.BACKUP_DIR, config.BACKUP_KEEP, tag="daily")
                log.info("სარეზერვო ასლი შეიქმნა: %s", path)
            except Exception:  # noqa: BLE001
                log.exception("სარეზერვო ასლის შექმნა ვერ მოხერხდა")


def create_app(session_factory: sessionmaker | None = None, background_tasks: bool = True,
               secret_key: str | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(_periodic_backup()) if background_tasks else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title=t("app.name"), lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.session_factory = session_factory or get_session_factory()
    app.add_middleware(SessionMiddleware, secret_key=secret_key or config.get_secret_key(),
                       session_cookie="pa_session", same_site="lax")
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
    app.include_router(system.router)
    for module in (ingredients, api, products, recipes, receipts, production, dispatch, inventory, reports):
        app.include_router(module.router)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return render(request, "error.html", {"title": t("error.404_title"), "text": t("error.404_text")},
                          status_code=404)
        return render(request, "error.html", {"title": t("error.500_title"), "text": str(exc.detail)},
                      status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.exception("მოულოდნელი შეცდომა: %s", request.url)
        return render(request, "error.html", {"title": t("error.500_title"), "text": t("common.unexpected_error")},
                      status_code=500)

    return app
