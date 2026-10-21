"""ბაზის მომზადება გაშვებისას: სარეზერვო ასლი → მიგრაციები → საწყისი მონაცემები."""
import logging

from alembic import command
from alembic.config import Config

from app import config
from app.db import get_engine, get_session_factory
from app.services import backup
from app.services.units import ensure_default_units

log = logging.getLogger(__name__)


def alembic_config() -> Config:
    cfg = Config(str(config.BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BASE_DIR / "alembic"))
    return cfg


def startup_backup() -> None:
    db_path = backup.sqlite_path_from_url(config.DATABASE_URL)
    if db_path is None or not db_path.exists():
        return
    path = backup.create_backup(db_path, config.BACKUP_DIR, config.BACKUP_KEEP, tag="start")
    log.info("სარეზერვო ასლი: %s", path)


def migrate() -> None:
    cfg = alembic_config()
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def ensure_reference_data() -> None:
    session = get_session_factory()()
    try:
        ensure_default_units(session)
        session.commit()
    finally:
        session.close()


def prepare_database() -> None:
    config.ensure_dirs()
    startup_backup()  # ასლი მიგრაციამდე — თუ რამე არასწორად წავა, ძველი ბაზა დაცულია
    migrate()
    ensure_reference_data()
