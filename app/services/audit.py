"""ცვლილებების ჟურნალი (AuditLog)."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import AuditLog, User


def diff(before: dict | None, after: dict | None) -> dict:
    """{"ველი": [ძველი, ახალი]} — მხოლოდ შეცვლილი ველები."""
    before = before or {}
    after = after or {}
    out = {}
    for key in list(before.keys()) + [k for k in after.keys() if k not in before]:
        old, new = before.get(key), after.get(key)
        if old != new:
            out[key] = [old, new]
    return out


def log(
    session: Session,
    *,
    action: str,
    entity: str,
    entity_id: int | None,
    label: str = "",
    before: dict | None = None,
    after: dict | None = None,
    user: User | None = None,
) -> AuditLog | None:
    changes = diff(before, after)
    if action == "update" and not changes:
        return None  # არაფერი შეცვლილა — ჟურნალს არ ვავსებთ ცარიელი ჩანაწერებით
    entry = AuditLog(
        action=action,
        entity=entity,
        entity_id=entity_id,
        entity_label=label[:300],
        changes=json.dumps(changes, ensure_ascii=False, default=str),
        user_id=user.id if user else None,
        username=user.username if user else None,
    )
    session.add(entry)
    return entry


def entries_for(session: Session, entity: str, entity_id: int, limit: int = 50) -> list[AuditLog]:
    return list(session.scalars(
        select(AuditLog)
        .where(AuditLog.entity == entity, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ))


def recent(session: Session, entity: str | None = None, limit: int = 200) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    return list(session.scalars(stmt))


def parsed_changes(entry: AuditLog) -> dict:
    try:
        return json.loads(entry.changes or "{}")
    except ValueError:
        return {}
