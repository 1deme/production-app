"""საბუთების საერთო ფუნქციები: ნომრები, კონტრაგენტები, სტატუსები."""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.documents import DocStatus, DocumentSequence
from app.models.products import Counterparty
from app.services import settings
from app.services.errors import BusinessError
from app.services.text import clean_name, normalize_text

# საბუთის ნომრის პრეფიქსი: მ-2026-0001
PREFIXES = {"receipt": "მ", "production": "წ", "dispatch": "გ", "inventory": "ი"}


def next_number(session: Session, doc_type: str, on: date) -> str:
    seq = session.get(DocumentSequence, (doc_type, on.year))
    if seq is None:
        seq = DocumentSequence(doc_type=doc_type, year=on.year, last_number=0)
        session.add(seq)
    seq.last_number += 1
    session.flush()
    return f"{PREFIXES[doc_type]}-{on.year}-{seq.last_number:04d}"


def find_or_create_counterparty(session: Session, name: str | None, *, supplier: bool = False,
                                customer: bool = False) -> Counterparty | None:
    name = clean_name(name)
    if not name:
        return None
    cp = session.scalar(select(Counterparty).where(Counterparty.search_name == normalize_text(name)))
    if cp is None:
        cp = Counterparty(name=name[:200], search_name=normalize_text(name)[:200])
        session.add(cp)
    if supplier:
        cp.is_supplier = True
    if customer:
        cp.is_customer = True
    session.flush()
    return cp


def counterparty_names(session: Session, *, supplier: bool = False, customer: bool = False) -> list[str]:
    stmt = select(Counterparty.name).order_by(Counterparty.search_name)
    if supplier:
        stmt = stmt.where(Counterparty.is_supplier.is_(True))
    if customer:
        stmt = stmt.where(Counterparty.is_customer.is_(True))
    return list(session.scalars(stmt))


def require_not_cancelled(doc) -> None:
    if doc.status == DocStatus.CANCELLED:
        raise BusinessError("docs.cancelled_readonly")


def require_draft(doc) -> None:
    if doc.status != DocStatus.DRAFT:
        raise BusinessError("docs.only_draft")


def mark_confirmed(doc) -> None:
    doc.status = DocStatus.CONFIRMED
    doc.confirmed_at = datetime.now()


def allow_negative_ingredients(session: Session) -> bool:
    """პარამეტრი: ინგრედიენტების ნაკლებობისას წარმოება იბლოკება (block) თუ გაფრთხილებით დაიშვება (warn)."""
    return settings.get(session, "production_shortage") == "warn"
