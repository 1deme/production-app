"""ნაშთები და მოძრაობის ჟურნალი.

ნაშთი არასოდეს ინახება ცალკე — ყოველთვის ითვლება stock_movements-ის ჯამიდან.
ამიტომ საბუთის რედაქტირება/გაუქმება უბრალოდ მისი მოძრაობების წაშლა და ხელახლა შექმნაა.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, not_, select
from sqlalchemy.orm import Session

from app.formatting import fmt_date, fmt_qty
from app.models.documents import ItemType, MovementType, StockMovement
from app.models.ingredients import Ingredient
from app.models.products import Product
from app.services.errors import ErrorBag

ZERO = Decimal("0.000")

DOC_URLS = {
    "receipt": "/receipts/{id}",
    "production": "/production/{id}",
    "dispatch": "/dispatch/{id}",
    "inventory": "/inventory/{id}",
}


def doc_url(doc_type: str, doc_id: int) -> str:
    return DOC_URLS.get(doc_type, "#").format(id=doc_id)


def _col(item_type: ItemType):
    return StockMovement.ingredient_id if item_type == ItemType.INGREDIENT else StockMovement.product_id


def _model(item_type: ItemType):
    return Ingredient if item_type == ItemType.INGREDIENT else Product


def _exclude(exclude_doc: tuple[str, int] | None):
    if not exclude_doc:
        return None
    return not_(and_(StockMovement.doc_type == exclude_doc[0], StockMovement.doc_id == exclude_doc[1]))


# ---------------------------------------------------------------- ნაშთები

def balances(session: Session, item_type: ItemType, *, as_of: date | None = None, ids=None,
             exclude_doc: tuple[str, int] | None = None) -> dict[int, Decimal]:
    """{item_id: ნაშთი საბაზო ერთეულში}. ვისაც მოძრაობა არ აქვს — სიაში არ არის (ე.ი. 0)."""
    col = _col(item_type)
    stmt = select(col, func.sum(StockMovement.qty)).where(col.is_not(None)).group_by(col)
    if as_of:
        stmt = stmt.where(StockMovement.date <= as_of)
    if ids is not None:
        stmt = stmt.where(col.in_(list(ids)))
    ex = _exclude(exclude_doc)
    if ex is not None:
        stmt = stmt.where(ex)
    return {item_id: total or ZERO for item_id, total in session.execute(stmt)}


def balance(session: Session, item_type: ItemType, item_id: int, **kw) -> Decimal:
    return balances(session, item_type, ids=[item_id], **kw).get(item_id, ZERO)


# ---------------------------------------------------------------- ჩაწერა / წაშლა

def add(session: Session, *, item_type: ItemType, item_id: int, on: date, qty: Decimal,
        movement_type: MovementType, doc_type: str, doc_id: int, doc_number: str,
        unit_cost: Decimal | None = None, amount: Decimal | None = None) -> StockMovement:
    m = StockMovement(
        item_type=item_type,
        ingredient_id=item_id if item_type == ItemType.INGREDIENT else None,
        product_id=item_id if item_type == ItemType.PRODUCT else None,
        date=on, qty=qty, movement_type=movement_type, unit_cost=unit_cost, amount=amount,
        doc_type=doc_type, doc_id=doc_id, doc_number=doc_number,
    )
    session.add(m)
    return m


def remove_doc(session: Session, doc_type: str, doc_id: int) -> set[tuple[ItemType, int]]:
    """საბუთის ყველა მოძრაობის წაშლა. აბრუნებს შეხებულ საქონელს (შემდგომი შემოწმებისთვის)."""
    affected = set()
    for m in session.scalars(select(StockMovement).where(StockMovement.doc_type == doc_type,
                                                         StockMovement.doc_id == doc_id)):
        affected.add((m.item_type, m.ingredient_id or m.product_id))
        session.delete(m)
    session.flush()
    return affected


def doc_items(session: Session, doc_type: str, doc_id: int) -> set[tuple[ItemType, int]]:
    rows = session.execute(select(StockMovement.item_type, StockMovement.ingredient_id, StockMovement.product_id)
                           .where(StockMovement.doc_type == doc_type, StockMovement.doc_id == doc_id))
    return {(t, i or p) for t, i, p in rows}


def check_non_negative(session: Session, items: set[tuple[ItemType, int]], *,
                       allow_negative_ingredients: bool = False) -> None:
    """ამოწმებს, რომ ნაშთი არცერთი დღის ბოლოს არ გახდეს უარყოფითი.

    დღის დონეზე ვამოწმებთ (და არა თითო მოძრაობაზე), რომ ერთსა და იმავე დღეს
    შეყვანილი მიღება და წარმოება თანმიმდევრობის მიუხედავად სწორად აღირიცხოს.
    """
    session.flush()
    errors = ErrorBag()
    for item_type, item_id in sorted(items, key=lambda x: (x[0].value, x[1])):
        if item_type == ItemType.INGREDIENT and allow_negative_ingredients:
            continue
        col = _col(item_type)
        rows = session.execute(select(StockMovement.date, func.sum(StockMovement.qty))
                               .where(col == item_id).group_by(StockMovement.date).order_by(StockMovement.date))
        running = ZERO
        for day, qty in rows:
            running += qty
            if running < 0:
                item = session.get(_model(item_type), item_id)
                errors.add("_doc", "stock.negative", name=item.name, date=fmt_date(day),
                           shortage=fmt_qty(-running, item.unit))
                break
    errors.raise_if_any()


# ---------------------------------------------------------------- ისტორია და რეპორტები

@dataclass
class HistoryRow:
    movement: StockMovement
    balance_after: Decimal


def history(session: Session, item_type: ItemType, item_id: int, limit: int = 300) -> list[HistoryRow]:
    col = _col(item_type)
    moves = list(session.scalars(select(StockMovement).where(col == item_id)
                                 .order_by(StockMovement.date.desc(), StockMovement.id.desc()).limit(limit)))
    running = balance(session, item_type, item_id)
    out = []
    for m in moves:
        out.append(HistoryRow(m, running))
        running -= m.qty
    return out


SUMMARY_KEYS = [t.value for t in MovementType]


def period_summary(session: Session, item_type: ItemType, start: date, end: date) -> dict[int, dict]:
    """{item_id: {opening, receipt, production_use, production_output, dispatch, adjustment, closing}}"""
    col = _col(item_type)
    data: dict[int, dict] = defaultdict(lambda: {k: ZERO for k in ["opening", *SUMMARY_KEYS, "closing"]})
    for item_id, total in session.execute(select(col, func.sum(StockMovement.qty))
                                          .where(col.is_not(None), StockMovement.date < start).group_by(col)):
        data[item_id]["opening"] = total
    for item_id, mtype, total in session.execute(
            select(col, StockMovement.movement_type, func.sum(StockMovement.qty))
            .where(col.is_not(None), StockMovement.date >= start, StockMovement.date <= end)
            .group_by(col, StockMovement.movement_type)):
        data[item_id][mtype.value] = total
    for row in data.values():
        row["closing"] = row["opening"] + sum(row[k] for k in SUMMARY_KEYS)
    return dict(data)
