"""ინვენტარიზაცია: ფაქტობრივი ნაშთის შეყვანა.

დადასტურებისას თითო სტრიქონზე: სხვაობა = ფაქტობრივი − სისტემური (საბუთის თარიღისთვის)
და სხვაობა იწერება „კორექტირების“ მოძრაობად. დადასტურებული ინვენტარიზაცია არ რედაქტირდება —
საჭიროების შემთხვევაში უქმდება და იქმნება ახალი (ასე ისტორია გასაგები რჩება).
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db_types import q_money
from app.formatting import fmt_date, fmt_qty
from app.models.documents import DocStatus, InventoryCount, InventoryLine, ItemType, MovementType
from app.models.ingredients import Ingredient
from app.models.products import Product
from app.models.system import User
from app.services import audit, stock
from app.services.costing import ingredient_costs, product_costs
from app.services.documents import (allow_negative_ingredients, mark_confirmed, next_number, require_draft,
                                    require_not_cancelled)
from app.services.errors import BusinessError, ErrorBag
from app.services.i18n_labels import status_label
from app.services.text import clean_name
from app.services.units import to_base

DOC = "inventory"


@dataclass
class InventoryInput:
    date: date | None
    item_type: ItemType
    note: str = ""
    actual: dict[int, Decimal] = field(default_factory=dict)  # {item_id: ფაქტობრივი, ჩვენების ერთეულში}


def snapshot(c: InventoryCount) -> dict:
    return {
        "number": c.number,
        "date": fmt_date(c.date),
        "status": status_label(c.status),
        "note": c.note or "",
        "lines": "; ".join(f"{ln.item.name} {fmt_qty(ln.actual_qty_base, ln.item.unit)}" for ln in c.lines),
    }


def _model(item_type: ItemType):
    return Ingredient if item_type == ItemType.INGREDIENT else Product


def save_count(session: Session, data: InventoryInput, count: InventoryCount | None = None, *,
               confirm: bool = False, user: User | None = None) -> InventoryCount:
    if count is not None:
        require_draft(count)  # დადასტურებული არ რედაქტირდება
    errors = ErrorBag()
    if data.date is None:
        errors.add("date", "common.required")
    if not data.actual:
        errors.add("lines", "inventory.no_lines")
    model = _model(data.item_type)
    built = []
    for item_id, qty in data.actual.items():
        item = session.get(model, item_id)
        if item is None:
            continue
        if qty < 0:
            errors.add(f"item:{item_id}", "errors.negative_not_allowed")
            continue
        built.append((item, to_base(qty, item.unit)))
    errors.raise_if_any()

    is_new = count is None
    before = None if is_new else snapshot(count)
    if is_new:
        count = InventoryCount(number=next_number(session, DOC, data.date), status=DocStatus.DRAFT,
                               item_type=data.item_type)
        session.add(count)
    count.date = data.date
    count.note = clean_name(data.note) or None
    count.lines.clear()
    session.flush()
    for pos, (item, qty_base) in enumerate(sorted(built, key=lambda x: x[0].search_name)):
        line = InventoryLine(position=pos, actual_qty_base=qty_base)
        if data.item_type == ItemType.INGREDIENT:
            line.ingredient = item
        else:
            line.product = item
        count.lines.append(line)
    session.flush()
    audit.log(session, action="create" if is_new else "update", entity=DOC, entity_id=count.id, label=count.number,
              before=before, after=snapshot(count), user=user)
    if confirm:
        confirm_count(session, count, user)
    return count


def confirm_count(session: Session, c: InventoryCount, user: User | None = None) -> None:
    require_draft(c)
    if not c.lines:
        raise BusinessError("inventory.no_lines")
    before = snapshot(c)
    stock.remove_doc(session, DOC, c.id)
    ids = [ln.item.id for ln in c.lines]
    system = stock.balances(session, c.item_type, as_of=c.date, ids=ids, exclude_doc=(DOC, c.id))
    if c.item_type == ItemType.INGREDIENT:
        costs = ingredient_costs(session, as_of=c.date, ids=ids)
    else:
        costs = product_costs(session, as_of=c.date, ids=ids)
    affected = set()
    for ln in c.lines:
        item_id = ln.item.id
        ln.system_qty_base = system.get(item_id, Decimal("0.000"))
        ln.diff_qty_base = ln.actual_qty_base - ln.system_qty_base
        if ln.diff_qty_base != 0:
            uc = costs.get(item_id, Decimal(0))
            stock.add(session, item_type=c.item_type, item_id=item_id, on=c.date, qty=ln.diff_qty_base,
                      movement_type=MovementType.ADJUSTMENT, doc_type=DOC, doc_id=c.id, doc_number=c.number,
                      unit_cost=uc, amount=q_money(ln.diff_qty_base * uc))
            affected.add((c.item_type, item_id))
    mark_confirmed(c)
    stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))
    audit.log(session, action="confirm", entity=DOC, entity_id=c.id, label=c.number, before=before,
              after=snapshot(c), user=user)


def cancel_count(session: Session, c: InventoryCount, user: User | None = None) -> None:
    require_not_cancelled(c)
    before = snapshot(c)
    affected = stock.remove_doc(session, DOC, c.id)
    c.status = DocStatus.CANCELLED
    stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))
    audit.log(session, action="cancel", entity=DOC, entity_id=c.id, label=c.number, before=before,
              after=snapshot(c), user=user)


def delete_count(session: Session, c: InventoryCount, user: User | None = None) -> None:
    require_draft(c)
    before, cid, label = snapshot(c), c.id, c.number
    session.delete(c)
    session.flush()
    audit.log(session, action="delete", entity=DOC, entity_id=cid, label=label, before=before, user=user)
