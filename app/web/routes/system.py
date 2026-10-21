"""მთავარი გვერდი, პარამეტრები და ცვლილებების ჟურნალი."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import config
from app.i18n import t
from app.models.system import User
from app.services import audit, backup, demo
from app.services.errors import BusinessError
from app.services import settings as settings_svc
from app.services import ingredients as ingredient_svc
from app.services.units import all_units, kind_label
from app.web.deps import get_current_user, get_db
from app.web.templating import flash, render

router = APIRouter()

AUDIT_ENTITIES = ["ingredient", "product", "recipe", "receipt", "production", "dispatch", "inventory", "settings"]
ENTITY_URLS = {"ingredient": "/ingredients/{id}", "product": "/products/{id}", "recipe": "/recipes/{id}",
               "receipt": "/receipts/{id}", "production": "/production/{id}", "dispatch": "/dispatch/{id}",
               "inventory": "/inventory/{id}"}


def entity_url(entry) -> str | None:
    if entry.action == "delete" or entry.entity not in ENTITY_URLS or entry.entity_id is None:
        return None
    return ENTITY_URLS[entry.entity].format(id=entry.entity_id)


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    from app.services.reports.data import dashboard as dashboard_data

    return render(request, "dashboard.html", {
        "active_nav": "dashboard",
        "counts": ingredient_svc.counts(db),
        "d": dashboard_data(db),
        "db_empty": demo.is_empty(db),
    })


@router.get("/audit")
def audit_log(request: Request, entity: str = "", db: Session = Depends(get_db)):
    if entity not in AUDIT_ENTITIES:
        entity = ""
    limit = 300
    entries = audit.recent(db, entity or None, limit=limit)
    return render(request, "audit/list.html", {
        "active_nav": "audit",
        "entries": entries,
        "entity": entity,
        "entities": AUDIT_ENTITIES,
        "limit": limit,
        "parse_changes": audit.parsed_changes,
        "entity_url": entity_url,
    })


def _db_path():
    return backup.sqlite_path_from_url(config.DATABASE_URL)


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "settings/index.html", {
        "active_nav": "settings",
        "backups": backup.list_backups(config.BACKUP_DIR),
        "backup_dir": config.BACKUP_DIR,
        "keep": config.BACKUP_KEEP,
        "db_path": _db_path(),
        "units": all_units(db),
        "kind_label": kind_label,
        "values": settings_svc.all_values(db),
        "db_empty": demo.is_empty(db),
        "options": settings_svc.OPTIONS,
    })


@router.post("/settings/general")
async def save_settings(request: Request, db: Session = Depends(get_db),
                        user: User | None = Depends(get_current_user)):
    form = await request.form()
    before = settings_svc.all_values(db)
    for key, (_, allowed) in settings_svc.OPTIONS.items():
        value = str(form.get(key, ""))
        if value in allowed:
            settings_svc.set_value(db, key, value)
    after = settings_svc.all_values(db)
    audit.log(db, action="update", entity="settings", entity_id=None, label=t("settings.title"),
              before={k: t(f"settings.{k}.{v}") for k, v in before.items()},
              after={k: t(f"settings.{k}.{v}") for k, v in after.items()}, user=user)
    db.commit()
    flash(request, t("settings.saved"))
    return RedirectResponse("/settings", status_code=303)


@router.post("/demo/load")
def demo_load(request: Request, db: Session = Depends(get_db)):
    try:
        demo.load_demo(db)
        db.commit()
    except BusinessError as e:
        db.rollback()
        flash(request, e.msg.render(), "danger")
        return RedirectResponse("/", status_code=303)
    flash(request, t("demo.loaded"))
    return RedirectResponse("/", status_code=303)


@router.post("/demo/clear")
def demo_clear(request: Request, db: Session = Depends(get_db)):
    if not demo.is_demo(db):
        flash(request, t("demo.not_demo"), "warning")
        return RedirectResponse("/", status_code=303)
    # წაშლამდე — სარეზერვო ასლი. თუ ასლი ვერ შეიქმნა, არაფერს ვშლით.
    db_path = _db_path()
    backup_name = "—"
    if db_path is not None:
        try:
            path = backup.create_backup(db_path, config.BACKUP_DIR, config.BACKUP_KEEP, tag="before-demo-clear")
            backup_name = path.name if path else "—"
        except Exception as e:  # noqa: BLE001
            flash(request, t("settings.backup_failed", error=str(e)), "danger")
            return RedirectResponse("/", status_code=303)
    try:
        demo.clear_demo(db)
        db.commit()
    except BusinessError as e:
        db.rollback()
        flash(request, e.msg.render(), "danger")
        return RedirectResponse("/", status_code=303)
    flash(request, t("demo.cleared", backup=backup_name))
    return RedirectResponse("/", status_code=303)


@router.post("/settings/backup")
def backup_now(request: Request):
    db_path = _db_path()
    if db_path is None:
        flash(request, t("settings.backup_not_sqlite"), "warning")
    else:
        try:
            path = backup.create_backup(db_path, config.BACKUP_DIR, config.BACKUP_KEEP, tag="manual")
            flash(request, t("settings.backup_created", name=path.name if path else "—"))
        except Exception as e:  # noqa: BLE001 — მომხმარებელს ვაჩვენებთ მიზეზს
            flash(request, t("settings.backup_failed", error=str(e)), "danger")
    return RedirectResponse("/settings", status_code=303)
