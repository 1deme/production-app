"""მზა პროდუქციის გატანა. გატანა ნაშთზე მეტი ვერასოდეს იქნება (პარამეტრის მიუხედავად)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db_types import q_money, q_price
from app.formatting import fmt_date, fmt_money, fmt_qty
from app.models.documents import Dispatch, DispatchLine, DocStatus, ItemType, MovementType
from app.models.products import Product
from app.models.system import User
from app.services import audit, stock
from app.services.costing import product_costs
from app.services.documents import (find_or_create_counterparty, mark_confirmed, next_number, require_draft,
                                    require_not_cancelled)
from app.services.errors import ErrorBag
from app.services.i18n_labels import status_label
from app.services.text import clean_name
from app.services.units import to_base

DOC = "dispatch"


@dataclass
class DispatchLineInput:
    product_id: int | None
    qty: Decimal | None  # პროდუქტის ჩვენების ერთეულში
    unit_price: Decimal | None = None  # არასავალდებულო, ჩვენების ერთეულზე


@dataclass
class DispatchInput:
    date: date | None
    customer: str = ""
    note: str = ""
    lines: list[DispatchLineInput] = field(default_factory=list)


def snapshot(d: Dispatch) -> dict:
    return {
        "number": d.number,
        "date": fmt_date(d.date),
        "counterparty": d.counterparty.name if d.counterparty else "",
        "status": status_label(d.status),
        "note": d.note or "",
        "lines": "; ".join(f"{ln.product.name} {fmt_qty(ln.qty_base, ln.product.unit)}"
                           + (f" × {fmt_money(ln.unit_price)}" if ln.unit_price is not None else "")
                           for ln in d.lines),
        "total": fmt_money(d.total),
    }


def _validate(session: Session, data: DispatchInput, existing: Dispatch | None) -> list[dict]:
    errors = ErrorBag()
    if data.date is None:
        errors.add("date", "common.required")
    if not data.lines:
        errors.add("lines", "docs.no_lines")
    old_ids = {ln.product_id for ln in existing.lines} if existing else set()
    seen = set()
    built = []
    for i, ln in enumerate(data.lines, start=1):
        key = f"line:{i}"
        p = session.get(Product, ln.product_id) if ln.product_id else None
        if p is None:
            errors.add(key, "docs.line_item_missing", n=i)
            continue
        if p.id in seen:
            errors.add(key, "dispatch.duplicate_product", n=i, name=p.name)
            continue
        seen.add(p.id)
        if not p.is_active and p.id not in old_ids:
            errors.add(key, "docs.line_inactive", n=i, name=p.name)
        if ln.qty is None or ln.qty <= 0:
            errors.add(key, "docs.line_qty_positive", n=i, name=p.name)
            continue
        qty_base = to_base(ln.qty, p.unit)
        price = q_price(ln.unit_price) if ln.unit_price is not None else None
        amount = q_money(ln.qty * price) if price is not None else None
        built.append({"product": p, "qty_base": qty_base, "unit_price": price, "amount": amount})
    errors.raise_if_any()
    return built


def _post(session: Session, d: Dispatch) -> set:
    affected = stock.remove_doc(session, DOC, d.id)
    costs = product_costs(session, as_of=d.date, ids=[ln.product_id for ln in d.lines])
    for ln in d.lines:
        uc = costs.get(ln.product_id, Decimal(0))
        stock.add(session, item_type=ItemType.PRODUCT, item_id=ln.product_id, on=d.date, qty=-ln.qty_base,
                  movement_type=MovementType.DISPATCH, doc_type=DOC, doc_id=d.id, doc_number=d.number,
                  unit_cost=uc, amount=-q_money(ln.qty_base * uc))
        affected.add((ItemType.PRODUCT, ln.product_id))
    return affected


def save_dispatch(session: Session, data: DispatchInput, dispatch: Dispatch | None = None, *,
                  confirm: bool = False, user: User | None = None) -> Dispatch:
    if dispatch is not None:
        require_not_cancelled(dispatch)
    built = _validate(session, data, dispatch)
    is_new = dispatch is None
    before = None if is_new else snapshot(dispatch)
    old_items = set() if is_new else stock.doc_items(session, DOC, dispatch.id)
    if is_new:
        dispatch = Dispatch(number=next_number(session, DOC, data.date), status=DocStatus.DRAFT)
        session.add(dispatch)
    dispatch.date = data.date
    dispatch.counterparty = find_or_create_counterparty(session, data.customer, customer=True)
    dispatch.note = clean_name(data.note) or None
    dispatch.lines.clear()
    session.flush()
    for pos, b in enumerate(built):
        dispatch.lines.append(DispatchLine(position=pos, **b))
    session.flush()
    if dispatch.status == DocStatus.CONFIRMED or confirm:
        if dispatch.status != DocStatus.CONFIRMED:
            mark_confirmed(dispatch)
        stock.check_non_negative(session, _post(session, dispatch) | old_items)
    audit.log(session, action="create" if is_new else "update", entity=DOC, entity_id=dispatch.id,
              label=dispatch.number, before=before, after=snapshot(dispatch), user=user)
    return dispatch


def confirm_dispatch(session: Session, d: Dispatch, user: User | None = None) -> None:
    require_draft(d)
    before = snapshot(d)
    mark_confirmed(d)
    stock.check_non_negative(session, _post(session, d))
    audit.log(session, action="confirm", entity=DOC, entity_id=d.id, label=d.number, before=before,
              after=snapshot(d), user=user)


def cancel_dispatch(session: Session, d: Dispatch, user: User | None = None) -> None:
    require_not_cancelled(d)
    before = snapshot(d)
    affected = stock.remove_doc(session, DOC, d.id)
    d.status = DocStatus.CANCELLED
    stock.check_non_negative(session, affected)
    audit.log(session, action="cancel", entity=DOC, entity_id=d.id, label=d.number, before=before,
              after=snapshot(d), user=user)


def delete_dispatch(session: Session, d: Dispatch, user: User | None = None) -> None:
    require_draft(d)
    before, did, label = snapshot(d), d.id, d.number
    session.delete(d)
    session.flush()
    audit.log(session, action="delete", entity=DOC, entity_id=did, label=label, before=before, user=user)
