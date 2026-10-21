"""Alembic-ის გარემო. SQLite-ზე გამოიყენება batch რეჟიმი (ALTER TABLE-ის შეზღუდვების გამო)."""
from alembic import context

import app.models  # noqa: F401 — ყველა მოდელის ჩატვირთვა
from app import config as app_config
from app.db import Base, make_engine
from app.db_types import ScaledDecimal

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    # ScaledDecimal ბაზაში უბრალოდ BIGINT-ია — მიგრაციები აპლიკაციის კოდზე არ უნდა იყოს დამოკიდებული
    if type_ == "type" and isinstance(obj, ScaledDecimal):
        return "sa.BigInteger()"
    return False


def _url() -> str:
    return context.config.attributes.get("url") or app_config.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(_url())
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True, render_item=render_item, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
