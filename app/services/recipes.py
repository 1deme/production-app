"""რეცეპტები და მათი ვერსიები.

- პროდუქტს აქვს ერთი აქტიური ვერსია; ძველი ვერსიები ისტორიისთვის რჩება.
- ვერსია, რომელიც უკვე გამოყენებულია წარმოებაში, „იკეტება“: შესაცვლელად იქმნება ახალი ვერსია.
  (თვითონ წარმოების საბუთები მაინც ინახავს ფაქტობრივ ხარჯს, ასე რომ ისტორია ორმაგად დაცულია.)
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db_types import q_qty
from app.formatting import fmt_num, fmt_qty
from app.models.documents import ProductionRun
from app.models.ingredients import Ingredient
from app.models.products import Product, Recipe, RecipeLine
from app.models.system import User
from app.models.units import Unit
from app.services import audit
from app.services.costing import waste_multiplier
from app.services.errors import BusinessError, ErrorBag
from app.services.text import clean_name
from app.services.units import to_base

ENTITY = "recipe"
MAX_WASTE = Decimal(90)


@dataclass
class RecipeLineInput:
    ingredient_id: int | None
    qty: Decimal | None
    unit_id: int | None


@dataclass
class RecipeInput:
    yield_qty: Decimal | None  # პროდუქტის ჩვენების ერთეულში
    waste_percent: Decimal | None = Decimal(0)
    note: str = ""
    lines: list[RecipeLineInput] = field(default_factory=list)


def is_recipe_used(session: Session, recipe_id: int) -> bool:
    return bool(session.scalar(select(exists().where(ProductionRun.recipe_id == recipe_id))))


def snapshot(r: Recipe) -> dict:
    return {
        "yield": fmt_qty(r.yield_qty, r.product.unit),
        "waste_percent": fmt_num(r.waste_percent) + "%",
        "lines": "; ".join(f"{ln.ingredient.name} {fmt_num(ln.qty, 3)} {ln.unit.name}" for ln in r.lines),
        "is_active": r.is_active,
        "note": r.note or "",
    }


def _label(r: Recipe) -> str:
    return f"{r.product.name} · v{r.version}"


def _validate(session: Session, product: Product, data: RecipeInput) -> list[tuple[Ingredient, Decimal, Unit]]:
    errors = ErrorBag()
    if data.yield_qty is None or data.yield_qty <= 0:
        errors.add("yield_qty", "recipes.yield_positive")
    if data.waste_percent is None or data.waste_percent < 0 or data.waste_percent > MAX_WASTE:
        errors.add("waste_percent", "recipes.waste_range", max=fmt_num(MAX_WASTE))
    lines = []
    seen = set()
    for i, ln in enumerate(data.lines, start=1):
        key = f"line:{i}"
        ing = session.get(Ingredient, ln.ingredient_id) if ln.ingredient_id else None
        if ing is None:
            errors.add(key, "docs.line_item_missing", n=i)
            continue
        if ing.id in seen:
            errors.add(key, "recipes.duplicate_ingredient", n=i, name=ing.name)
        seen.add(ing.id)
        unit = session.get(Unit, ln.unit_id) if ln.unit_id else None
        if unit is None or unit.kind != ing.unit.kind:
            errors.add(key, "docs.line_bad_unit", n=i, name=ing.name)
        if ln.qty is None or ln.qty <= 0:
            errors.add(key, "docs.line_qty_positive", n=i, name=ing.name)
        if unit is not None and ln.qty and ln.qty > 0 and unit.kind == ing.unit.kind:
            lines.append((ing, ln.qty, unit))
    if not data.lines:
        errors.add("lines", "recipes.no_lines")
    errors.raise_if_any()
    return lines


def _apply(recipe: Recipe, product: Product, data: RecipeInput, lines) -> None:
    recipe.yield_qty = to_base(data.yield_qty, product.unit)
    recipe.waste_percent = data.waste_percent
    recipe.note = clean_name(data.note) or None
    recipe.lines.clear()
    for pos, (ing, qty, unit) in enumerate(lines):
        recipe.lines.append(RecipeLine(position=pos, ingredient=ing, qty=qty, unit=unit, qty_base=to_base(qty, unit)))


def _next_version(session: Session, product_id: int) -> int:
    return (session.scalar(select(func.max(Recipe.version)).where(Recipe.product_id == product_id)) or 0) + 1


def create_recipe(session: Session, product: Product, data: RecipeInput, user: User | None = None,
                  copied_from: Recipe | None = None) -> Recipe:
    lines = _validate(session, product, data)
    has_active = product.active_recipe is not None
    recipe = Recipe(product=product, version=_next_version(session, product.id), is_active=not has_active,
                    copied_from_id=copied_from.id if copied_from else None)
    _apply(recipe, product, data, lines)
    session.add(recipe)
    session.flush()
    audit.log(session, action="create", entity=ENTITY, entity_id=recipe.id, label=_label(recipe),
              after=snapshot(recipe), user=user)
    return recipe


def update_recipe(session: Session, recipe: Recipe, data: RecipeInput, user: User | None = None) -> Recipe:
    if is_recipe_used(session, recipe.id):
        raise BusinessError("recipes.locked_used")
    lines = _validate(session, recipe.product, data)
    before = snapshot(recipe)
    _apply(recipe, recipe.product, data, lines)
    session.flush()
    audit.log(session, action="update", entity=ENTITY, entity_id=recipe.id, label=_label(recipe),
              before=before, after=snapshot(recipe), user=user)
    return recipe


def to_input(recipe: Recipe) -> RecipeInput:
    return RecipeInput(
        yield_qty=recipe.yield_qty / recipe.product.unit.factor,
        waste_percent=recipe.waste_percent,
        note=recipe.note or "",
        lines=[RecipeLineInput(ln.ingredient_id, ln.qty, ln.unit_id) for ln in recipe.lines],
    )


def new_version(session: Session, recipe: Recipe, user: User | None = None) -> Recipe:
    """აქტიური/ძველი ვერსიის ასლი ახალ (ჯერ არააქტიურ) ვერსიად — რედაქტირებისთვის."""
    copy = Recipe(product=recipe.product, version=_next_version(session, recipe.product_id), is_active=False,
                  copied_from_id=recipe.id, yield_qty=recipe.yield_qty, waste_percent=recipe.waste_percent,
                  note=recipe.note)
    for ln in recipe.lines:
        copy.lines.append(RecipeLine(position=ln.position, ingredient_id=ln.ingredient_id, qty=ln.qty,
                                     unit_id=ln.unit_id, qty_base=ln.qty_base))
    session.add(copy)
    session.flush()
    session.refresh(copy)
    audit.log(session, action="create", entity=ENTITY, entity_id=copy.id, label=_label(copy),
              after=snapshot(copy), user=user)
    return copy


def copy_to_product(session: Session, recipe: Recipe, target: Product, user: User | None = None) -> Recipe:
    """რეცეპტის კოპირება სხვა პროდუქტზე. გამოსავალი გადაიყვანება, თუ ერთეულები თავსებადია."""
    if target.unit.kind != recipe.product.unit.kind:
        raise BusinessError("recipes.copy_unit_mismatch", source=recipe.product.unit.name, target=target.unit.name)
    data = to_input(recipe)
    data.yield_qty = recipe.yield_qty / target.unit.factor
    return create_recipe(session, target, data, user, copied_from=recipe)


def activate(session: Session, recipe: Recipe, user: User | None = None) -> None:
    if recipe.is_active:
        return
    for other in recipe.product.recipes:
        if other.is_active:
            other.is_active = False
            audit.log(session, action="deactivate", entity=ENTITY, entity_id=other.id, label=_label(other),
                      before={"is_active": True}, after={"is_active": False}, user=user)
    session.flush()
    recipe.is_active = True
    session.flush()
    audit.log(session, action="activate", entity=ENTITY, entity_id=recipe.id, label=_label(recipe),
              before={"is_active": False}, after={"is_active": True}, user=user)


def delete_recipe(session: Session, recipe: Recipe, user: User | None = None) -> None:
    if is_recipe_used(session, recipe.id):
        raise BusinessError("recipes.cannot_delete_used")
    if recipe.is_active and len(recipe.product.recipes) > 1:
        raise BusinessError("recipes.cannot_delete_active")
    before, rid, label = snapshot(recipe), recipe.id, _label(recipe)
    session.delete(recipe)
    session.flush()
    audit.log(session, action="delete", entity=ENTITY, entity_id=rid, label=label, before=before, user=user)


def consumption(recipe: Recipe, product_qty_base: Decimal) -> list[tuple[Ingredient, Decimal]]:
    """რამდენი ინგრედიენტი ჩამოიწერება product_qty_base რაოდენობის წარმოებისთვის (ბრაკის ჩათვლით)."""
    batches = product_qty_base / recipe.yield_qty
    mult = waste_multiplier(recipe)
    return [(ln.ingredient, q_qty(ln.qty_base * batches * mult)) for ln in recipe.lines]
