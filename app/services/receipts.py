"""მიღების ზედნადები.

დრაფტი ნაშთზე გავლენას არ ახდენს. დადასტურებისას თითო სტრიქონზე იქმნება „მიღების“ მოძრაობა.
დადასტურებულის რედაქტირება ერთ ტრანზაქციაში: ძველი მოძრაობები იშლება → იქმნება ახალი →
მოწმდება, რომ ნაშთი არსად გახდა უარყოფითი (მაგ. თუ შემცირებული რაოდენობა უკვე დახარჯულია).
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db_types import q_money, q_price, q_unit_cost
from app.formatting import fmt_date, fmt_money, fmt_num
from app.models.documents import DocStatus, ItemType, MovementType, Receipt, ReceiptLine
from app.models.ingredients import Ingredient
from app.models.system import User
from app.models.units import Unit
from app.services import audit, stock
from app.services.documents import (allow_negative_ingredients, find_or_create_counterparty, mark_confirmed,
                                    next_number, require_draft, require_not_cancelled)
from app.services.errors import ErrorBag
from app.services.i18n_labels import status_label
from app.services.text import clean_name
from app.services.units import to_base

DOC = "receipt"


@dataclass
class ReceiptLineInput:
    ingredient_id: int | None
    qty: Decimal | None
    unit_id: int | None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    source: str = "price"  # რომელი ველი შეიყვანა მომხმარებელმა ბოლოს: price ან amount


@dataclass
class ReceiptInput:
    date: date | None
    supplier: str = ""
    note: str = ""
    lines: list[ReceiptLineInput] = field(default_factory=list)


def snapshot(r: Receipt) -> dict:
    return {
        "number": r.number,
        "date": fmt_date(r.date),
        "counterparty": r.counterparty.name if r.counterparty else "",
        "status": status_label(r.status),
        "note": r.note or "",
        "lines": "; ".join(f"{ln.ingredient.name} {fmt_num(ln.qty, 3)} {ln.unit.name} × {fmt_num(ln.unit_price, 4)}"
                           f" = {fmt_money(ln.amount)}" for ln in r.lines),
        "total": fmt_money(r.total),
    }


def _validate(session: Session, data: ReceiptInput, existing: Receipt | None) -> list[dict]:
    errors = ErrorBag()
    if data.date is None:
        errors.add("date", "common.required")
    if not data.lines:
        errors.add("lines", "docs.no_lines")
    old_ids = {ln.ingredient_id for ln in existing.lines} if existing else set()
    built = []
    for i, ln in enumerate(data.lines, start=1):
        key = f"line:{i}"
        ing = session.get(Ingredient, ln.ingredient_id) if ln.ingredient_id else None
        if ing is None:
            errors.add(key, "docs.line_item_missing", n=i)
            continue
        if not ing.is_active and ing.id not in old_ids:
            errors.add(key, "docs.line_inactive", n=i, name=ing.name)
        unit = session.get(Unit, ln.unit_id) if ln.unit_id else None
        if unit is None or unit.kind != ing.unit.kind:
            errors.add(key, "docs.line_bad_unit", n=i, name=ing.name)
            continue
        if ln.qty is None or ln.qty <= 0:
            errors.add(key, "docs.line_qty_positive", n=i, name=ing.name)
            continue
        use_amount = ln.source == "amount" and ln.amount is not None
        if not use_amount and ln.unit_price is None:
            if ln.amount is not None:
                use_amount = True
            else:
                errors.add(key, "receipts.line_price_required", n=i, name=ing.name)
                continue
        if use_amount:
            amount = q_money(ln.amount)
            price = q_price(amount / ln.qty)
        else:
            price = q_price(ln.unit_price)
            amount = q_money(ln.qty * price)
        if amount < 0 or price < 0:
            errors.add(key, "errors.negative_not_allowed")
            continue
        qty_base = to_base(ln.qty, unit)
        if qty_base <= 0:
            errors.add(key, "docs.line_qty_positive", n=i, name=ing.name)
            continue
        built.append({"ingredient": ing, "qty": ln.qty, "unit": unit, "qty_base": qty_base,
                      "unit_price": price, "amount": amount})
    errors.raise_if_any()
    return built


def _post(session: Session, r: Receipt) -> set:
    """მოძრაობების (ხელახალი) შექმნა დადასტურებული ზედნადებისთვის."""
    affected = stock.remove_doc(session, DOC, r.id)
    for ln in r.lines:
        stock.add(session, item_type=ItemType.INGREDIENT, item_id=ln.ingredient_id, on=r.date, qty=ln.qty_base,
                  movement_type=MovementType.RECEIPT, doc_type=DOC, doc_id=r.id, doc_number=r.number,
                  unit_cost=q_unit_cost(ln.amount / ln.qty_base), amount=ln.amount)
        affected.add((ItemType.INGREDIENT, ln.ingredient_id))
    return affected


def save_receipt(session: Session, data: ReceiptInput, receipt: Receipt | None = None, *,
                 confirm: bool = False, user: User | None = None) -> Receipt:
    if receipt is not None:
        require_not_cancelled(receipt)
    built = _validate(session, data, receipt)
    is_new = receipt is None
    before = None if is_new else snapshot(receipt)
    old_items = set() if is_new else stock.doc_items(session, DOC, receipt.id)
    if is_new:
        receipt = Receipt(number=next_number(session, DOC, data.date), status=DocStatus.DRAFT)
        session.add(receipt)
    receipt.date = data.date
    receipt.counterparty = find_or_create_counterparty(session, data.supplier, supplier=True)
    receipt.note = clean_name(data.note) or None
    receipt.lines.clear()
    session.flush()
    for pos, b in enumerate(built):
        receipt.lines.append(ReceiptLine(position=pos, **b))
    session.flush()

    if receipt.status == DocStatus.CONFIRMED or confirm:
        if receipt.status != DocStatus.CONFIRMED:
            mark_confirmed(receipt)
        affected = _post(session, receipt) | old_items
        stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))

    audit.log(session, action="create" if is_new else "update", entity=DOC, entity_id=receipt.id,
              label=receipt.number, before=before, after=snapshot(receipt), user=user)
    return receipt


def confirm_receipt(session: Session, r: Receipt, user: User | None = None) -> None:
    require_draft(r)
    before = snapshot(r)
    mark_confirmed(r)
    affected = _post(session, r)
    stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))
    audit.log(session, action="confirm", entity=DOC, entity_id=r.id, label=r.number, before=before,
              after=snapshot(r), user=user)


def cancel_receipt(session: Session, r: Receipt, user: User | None = None) -> None:
    require_not_cancelled(r)
    before = snapshot(r)
    affected = stock.remove_doc(session, DOC, r.id)
    r.status = DocStatus.CANCELLED
    stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))
    audit.log(session, action="cancel", entity=DOC, entity_id=r.id, label=r.number, before=before,
              after=snapshot(r), user=user)


def delete_receipt(session: Session, r: Receipt, user: User | None = None) -> None:
    require_draft(r)
    before, rid, label = snapshot(r), r.id, r.number
    session.delete(r)
    session.flush()
    audit.log(session, action="delete", entity=DOC, entity_id=rid, label=label, before=before, user=user)
