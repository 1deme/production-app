"""მზა პროდუქციის კატალოგი."""
from dataclasses import dataclass

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.documents import DispatchLine, InventoryLine, ProductionRun, StockMovement
from app.models.ingredients import Ingredient, IngredientBarcode
from app.models.products import Product, Recipe
from app.models.system import User
from app.models.units import Unit
from app.services import audit
from app.services.errors import BusinessError, ErrorBag
from app.services.text import barcode_key, clean_barcode, clean_name, escape_like, normalize_text
from app.services.units import kind_label

ENTITY = "product"


@dataclass
class ProductInput:
    name: str
    unit_id: int | None
    code: str = ""
    barcode: str = ""
    note: str = ""


def is_product_used(session: Session, product_id: int) -> bool:
    checks = [
        exists().where(Recipe.product_id == product_id),
        exists().where(ProductionRun.product_id == product_id),
        exists().where(DispatchLine.product_id == product_id),
        exists().where(InventoryLine.product_id == product_id),
        exists().where(StockMovement.product_id == product_id),
    ]
    return bool(session.scalar(select(or_(*checks))))


def snapshot(p: Product) -> dict:
    return {"name": p.name, "code": p.code or "", "barcode": p.barcode or "", "unit": p.unit.name,
            "is_active": p.is_active, "note": p.note or ""}


def _validate(session: Session, data: ProductInput, existing: Product | None):
    errors = ErrorBag()
    own_id = existing.id if existing else -1
    name = clean_name(data.name)
    if not name:
        errors.add("name", "common.required")
    elif len(name) > 200:
        errors.add("name", "ingredients.name_too_long", max=200)
    else:
        dup = session.scalar(select(Product).where(Product.search_name == normalize_text(name), Product.id != own_id))
        if dup:
            errors.add("name", "products.duplicate_name", name=dup.name)
    unit = session.get(Unit, data.unit_id) if data.unit_id else None
    if unit is None:
        errors.add("unit_id", "errors.unknown_unit")
    elif existing and unit.kind != existing.unit.kind and is_product_used(session, existing.id):
        errors.add("unit_id", "products.unit_kind_locked",
                   old=kind_label(existing.unit.kind), new=kind_label(unit.kind))
    barcode = clean_barcode(data.barcode)[:64]
    key = barcode_key(barcode) if barcode else None
    if key:
        other = session.scalar(select(Product).where(Product.barcode_norm == key, Product.id != own_id))
        if other:
            errors.add("barcode", "products.duplicate_barcode", barcode=barcode, name=other.name)
        ing = session.scalar(select(Ingredient).join(IngredientBarcode).where(IngredientBarcode.code_norm == key))
        if ing:
            errors.add("barcode", "ingredients.duplicate_barcode", barcode=barcode, name=ing.name)
    errors.raise_if_any()
    return name, unit, barcode, key


def _apply(p: Product, name, unit, barcode, key, data: ProductInput):
    p.name = name
    p.search_name = normalize_text(name)
    p.unit = unit
    p.code = clean_name(data.code)[:64] or None
    p.barcode = barcode or None
    p.barcode_norm = key
    p.note = clean_name(data.note) or None


def create_product(session: Session, data: ProductInput, user: User | None = None) -> Product:
    vals = _validate(session, data, None)
    p = Product(is_active=True)
    _apply(p, *vals, data)
    session.add(p)
    session.flush()
    audit.log(session, action="create", entity=ENTITY, entity_id=p.id, label=p.name, after=snapshot(p), user=user)
    return p


def update_product(session: Session, p: Product, data: ProductInput, user: User | None = None) -> Product:
    vals = _validate(session, data, p)
    before = snapshot(p)
    _apply(p, *vals, data)
    session.flush()
    audit.log(session, action="update", entity=ENTITY, entity_id=p.id, label=p.name, before=before,
              after=snapshot(p), user=user)
    return p


def set_active(session: Session, p: Product, active: bool, user: User | None = None) -> None:
    if p.is_active == active:
        return
    before = snapshot(p)
    p.is_active = active
    session.flush()
    audit.log(session, action="activate" if active else "deactivate", entity=ENTITY, entity_id=p.id,
              label=p.name, before=before, after=snapshot(p), user=user)


def delete_product(session: Session, p: Product, user: User | None = None) -> None:
    if is_product_used(session, p.id):
        raise BusinessError("products.cannot_delete_used", name=p.name)
    before, pid, label = snapshot(p), p.id, p.name
    session.delete(p)
    session.flush()
    audit.log(session, action="delete", entity=ENTITY, entity_id=pid, label=label, before=before, user=user)


def search_products(session: Session, query: str = "", status: str = "active", limit: int = 200) -> list[Product]:
    stmt = select(Product)
    if status == "active":
        stmt = stmt.where(Product.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Product.is_active.is_(False))
    qn = normalize_text(query)
    if qn:
        # დასახელება (ყველა სიტყვა) ან კოდი ან შტრიხკოდი (ნაწილობრივიც)
        name_match = and_(*[Product.search_name.like(f"%{escape_like(w)}%", escape="\\") for w in qn.split(" ")])
        alts = [name_match, func.lower(func.coalesce(Product.code, "")) == qn]
        key = barcode_key(query)
        if key:
            alts.append(Product.barcode_norm.like(f"%{escape_like(key)}%", escape="\\"))
        stmt = stmt.where(or_(*alts))
    return list(session.scalars(stmt.order_by(Product.search_name).limit(limit)))
