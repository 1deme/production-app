"""ინგრედიენტების ბიზნეს-ლოგიკა: შექმნა, რედაქტირება, ძიება, დეაქტივაცია.

სერვისები არასოდეს აკეთებენ commit-ს — ტრანზაქციას მართავს გამომძახებელი,
რომ რამდენიმე მოქმედება ერთ ტრანზაქციაში მოექცეს (ან ყველაფერი, ან არაფერი).
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import and_, case, exists, func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.formatting import fmt_qty
from app.models.ingredients import Ingredient, IngredientBarcode
from app.models.system import User
from app.models.units import Unit
from app.services import audit
from app.services.errors import BusinessError, ErrorBag
from app.services.text import barcode_key, clean_barcode, clean_name, escape_like, normalize_text
from app.services.units import kind_label, to_base

MAX_NAME = 200
MAX_BARCODE = 64
ENTITY = "ingredient"


@dataclass
class IngredientInput:
    name: str
    unit_id: int | None
    min_stock: Decimal = Decimal(0)  # არჩეულ ერთეულში (მაგ. 5 კგ)
    barcodes: list[str] = field(default_factory=list)
    note: str | None = None


# ---------------------------------------------------------------- გამოყენება

def is_ingredient_used(session: Session, ingredient_id: int) -> bool:
    """გამოყენებულია თუ არა საბუთებში/რეცეპტებში.

    გამოყენებული ინგრედიენტის წაშლა და ერთეულის ტიპის შეცვლა აკრძალულია.
    """
    from app.models.documents import InventoryLine, ProductionLine, ReceiptLine, StockMovement
    from app.models.products import RecipeLine

    checks = [
        exists().where(ReceiptLine.ingredient_id == ingredient_id),
        exists().where(RecipeLine.ingredient_id == ingredient_id),
        exists().where(ProductionLine.ingredient_id == ingredient_id),
        exists().where(InventoryLine.ingredient_id == ingredient_id),
        exists().where(StockMovement.ingredient_id == ingredient_id),
    ]
    return bool(session.scalar(select(or_(*checks))))


# ---------------------------------------------------------------- შენახვა

def snapshot(ing: Ingredient) -> dict:
    """ადამიანისთვის წასაკითხი მდგომარეობა ჟურნალისთვის."""
    return {
        "name": ing.name,
        "unit": ing.unit.name,
        "min_stock": fmt_qty(ing.min_stock, ing.unit),
        "barcodes": ", ".join(b.barcode for b in ing.barcodes),
        "is_active": ing.is_active,
        "note": ing.note or "",
    }


def _validate(session: Session, data: IngredientInput, existing: Ingredient | None) -> tuple[str, Unit | None, list[str]]:
    errors = ErrorBag()
    own_id = existing.id if existing else -1

    name = clean_name(data.name)
    if not name:
        errors.add("name", "common.required")
    elif len(name) > MAX_NAME:
        errors.add("name", "ingredients.name_too_long", max=MAX_NAME)
    else:
        dup = session.scalar(
            select(Ingredient).where(Ingredient.search_name == normalize_text(name), Ingredient.id != own_id)
        )
        if dup:
            errors.add("name", "ingredients.duplicate_name", name=dup.name)

    unit = session.get(Unit, data.unit_id) if data.unit_id else None
    if unit is None:
        errors.add("unit_id", "errors.unknown_unit")
    elif existing and unit.kind != existing.unit.kind and is_ingredient_used(session, existing.id):
        errors.add("unit_id", "ingredients.unit_kind_locked",
                   old=kind_label(existing.unit.kind), new=kind_label(unit.kind))

    if data.min_stock is None or data.min_stock < 0:
        errors.add("min_stock", "errors.negative_not_allowed")

    barcodes: list[str] = []
    seen: set[str] = set()
    for raw in data.barcodes:
        code = clean_barcode(raw)
        if not code:
            continue
        if len(code) > MAX_BARCODE:
            errors.add("barcodes", "ingredients.barcode_too_long", max=MAX_BARCODE)
            continue
        key = barcode_key(code)
        if key in seen:
            errors.add("barcodes", "ingredients.duplicate_barcode_in_form", barcode=code)
            continue
        seen.add(key)
        barcodes.append(code)
    if seen:
        taken = session.execute(
            select(IngredientBarcode.barcode, Ingredient.name)
            .join(Ingredient)
            .where(IngredientBarcode.code_norm.in_(seen), IngredientBarcode.ingredient_id != own_id)
        ).all()
        for code, owner in taken:
            errors.add("barcodes", "ingredients.duplicate_barcode", barcode=code, name=owner)
        from app.models.products import Product
        for code, owner in session.execute(select(Product.barcode, Product.name)
                                           .where(Product.barcode_norm.in_(seen))):
            errors.add("barcodes", "products.barcode_taken_by_product", barcode=code, name=owner)

    errors.raise_if_any()
    return name, unit, barcodes


def validate_ingredient(session: Session, data: IngredientInput, existing: Ingredient | None = None) -> None:
    """მხოლოდ შემოწმება (შენახვის გარეშე) — რომ ფორმაში ყველა შეცდომა ერთად ვაჩვენოთ."""
    _validate(session, data, existing)


def _sync_barcodes(ing: Ingredient, codes: list[str]) -> None:
    wanted = {barcode_key(c): c for c in codes}
    for bc in list(ing.barcodes):
        if bc.code_norm not in wanted:
            ing.barcodes.remove(bc)
        else:
            bc.barcode = wanted.pop(bc.code_norm)  # იგივე კოდი, შესაძლოა სხვა რეგისტრით
    for key, code in wanted.items():
        ing.barcodes.append(IngredientBarcode(barcode=code, code_norm=key))


def create_ingredient(session: Session, data: IngredientInput, user: User | None = None) -> Ingredient:
    name, unit, barcodes = _validate(session, data, None)
    ing = Ingredient(
        name=name,
        search_name=normalize_text(name),
        unit=unit,
        min_stock=to_base(data.min_stock, unit),
        note=clean_name(data.note) or None,
        is_active=True,
    )
    _sync_barcodes(ing, barcodes)
    session.add(ing)
    session.flush()
    audit.log(session, action="create", entity=ENTITY, entity_id=ing.id, label=ing.name,
              after=snapshot(ing), user=user)
    return ing


def update_ingredient(session: Session, ing: Ingredient, data: IngredientInput, user: User | None = None) -> Ingredient:
    name, unit, barcodes = _validate(session, data, ing)
    before = snapshot(ing)
    ing.name = name
    ing.search_name = normalize_text(name)
    ing.unit = unit
    ing.min_stock = to_base(data.min_stock, unit)
    ing.note = clean_name(data.note) or None
    _sync_barcodes(ing, barcodes)
    session.flush()
    audit.log(session, action="update", entity=ENTITY, entity_id=ing.id, label=ing.name,
              before=before, after=snapshot(ing), user=user)
    return ing


def set_active(session: Session, ing: Ingredient, active: bool, user: User | None = None) -> None:
    if ing.is_active == active:
        return
    before = snapshot(ing)
    ing.is_active = active
    session.flush()
    audit.log(session, action="activate" if active else "deactivate", entity=ENTITY, entity_id=ing.id,
              label=ing.name, before=before, after=snapshot(ing), user=user)


def delete_ingredient(session: Session, ing: Ingredient, user: User | None = None) -> None:
    if is_ingredient_used(session, ing.id):
        raise BusinessError("ingredients.cannot_delete_used", name=ing.name)
    before = snapshot(ing)
    ing_id, label = ing.id, ing.name
    session.delete(ing)
    session.flush()
    audit.log(session, action="delete", entity=ENTITY, entity_id=ing_id, label=label, before=before, user=user)


# ---------------------------------------------------------------- ძიება

@dataclass
class SearchResult:
    items: list[Ingredient]
    total: int
    exact_barcode: list[Ingredient]  # ზუსტი დამთხვევა შტრიხკოდით (სკანერისთვის)
    query: str = ""


def search_ingredients(
    session: Session,
    query: str = "",
    status: str = "active",
    limit: int = 200,
) -> SearchResult:
    """ერთი საძიებო ველი: დასახელება (ნაწილობრივიც, ყოველი სიტყვა) ან შტრიხკოდი (ნაწილობრივიც)."""
    conditions = []
    if status == "active":
        conditions.append(Ingredient.is_active.is_(True))
    elif status == "inactive":
        conditions.append(Ingredient.is_active.is_(False))

    qn = normalize_text(query)
    bkey = barcode_key(query)
    order = [Ingredient.search_name]
    if qn:
        words = qn.split(" ")
        name_match = and_(*[Ingredient.search_name.like(f"%{escape_like(w)}%", escape="\\") for w in words])
        barcode_match = Ingredient.barcodes.any(
            IngredientBarcode.code_norm.like(f"%{escape_like(bkey)}%", escape="\\")
        ) if bkey else None
        conditions.append(or_(name_match, barcode_match) if barcode_match is not None else name_match)
        exact_bc = Ingredient.barcodes.any(IngredientBarcode.code_norm == bkey)
        rank = case(
            (exact_bc, 0),
            (Ingredient.search_name == qn, 1),
            (Ingredient.search_name.like(f"{escape_like(qn)}%", escape="\\"), 2),
            else_=3,
        )
        order = [rank, Ingredient.search_name]

    where = and_(*conditions) if conditions else true()
    total = session.scalar(select(func.count(Ingredient.id)).where(where)) or 0
    items = list(session.scalars(
        select(Ingredient).where(where).options(selectinload(Ingredient.barcodes)).order_by(*order).limit(limit)
    ))
    exact = [i for i in items if bkey and any(b.code_norm == bkey for b in i.barcodes)]
    return SearchResult(items=items, total=total, exact_barcode=exact, query=query)


def find_by_barcode(session: Session, code: str) -> Ingredient | None:
    key = barcode_key(code)
    if not key:
        return None
    return session.scalar(
        select(Ingredient).join(IngredientBarcode).where(IngredientBarcode.code_norm == key)
    )


def counts(session: Session) -> dict:
    active = session.scalar(select(func.count(Ingredient.id)).where(Ingredient.is_active.is_(True))) or 0
    inactive = session.scalar(select(func.count(Ingredient.id)).where(Ingredient.is_active.is_(False))) or 0
    barcodes = session.scalar(select(func.count(IngredientBarcode.id))) or 0
    return {"active": active, "inactive": inactive, "barcodes": barcodes}
