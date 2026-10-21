"""თვითღირებულება.

ინგრედიენტის ფასი საბაზო ერთეულზე (₾/გ, ₾/მლ, ₾/ცალი) ითვლება დადასტურებული მიღებებიდან:
- „საშუალო შეწონილი“ (ნაგულისხმევი): ყველა მიღების ჯამური თანხა / ჯამური რაოდენობა მოცემულ თარიღამდე;
- „ბოლო ფასი“: ბოლო მიღების ფასი მოცემულ თარიღამდე.
წარმოების დადასტურებისას ფასი „იყინება“ საბუთში — მიღების ფასის შემდგომი შესწორება ძველ წარმოებას არ ცვლის.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db_types import q_money, q_unit_cost
from app.models.documents import MovementType, StockMovement
from app.models.ingredients import Ingredient
from app.models.products import Recipe, RecipeLine
from app.services import settings

METHODS = ("average", "last")


def cost_method(session: Session) -> str:
    m = settings.get(session, "cost_method")
    return m if m in METHODS else "average"


def ingredient_costs(session: Session, *, as_of: date | None = None, ids=None,
                     method: str | None = None) -> dict[int, Decimal]:
    method = method or cost_method(session)
    base = select(StockMovement.ingredient_id).where(StockMovement.movement_type == MovementType.RECEIPT,
                                                     StockMovement.ingredient_id.is_not(None),
                                                     StockMovement.qty > 0)
    if as_of:
        base = base.where(StockMovement.date <= as_of)
    if ids is not None:
        base = base.where(StockMovement.ingredient_id.in_(list(ids)))

    out: dict[int, Decimal] = {}
    if method == "average":
        stmt = base.add_columns(func.sum(StockMovement.amount), func.sum(StockMovement.qty)) \
            .group_by(StockMovement.ingredient_id)
        for ing_id, amount, qty in session.execute(stmt):
            if qty:
                out[ing_id] = q_unit_cost(amount / qty)
    else:
        stmt = base.add_columns(StockMovement.amount, StockMovement.qty) \
            .order_by(StockMovement.date.desc(), StockMovement.id.desc())
        for ing_id, amount, qty in session.execute(stmt):
            if ing_id not in out and qty:
                out[ing_id] = q_unit_cost(amount / qty)
    return out


def product_costs(session: Session, *, as_of: date | None = None, ids=None) -> dict[int, Decimal]:
    """მზა პროდუქციის საშუალო თვითღირებულება (წარმოების საბუთებიდან) საბაზო ერთეულზე."""
    stmt = select(StockMovement.product_id, func.sum(StockMovement.amount), func.sum(StockMovement.qty)) \
        .where(StockMovement.movement_type == MovementType.PRODUCTION_OUTPUT, StockMovement.product_id.is_not(None)) \
        .group_by(StockMovement.product_id)
    if as_of:
        stmt = stmt.where(StockMovement.date <= as_of)
    if ids is not None:
        stmt = stmt.where(StockMovement.product_id.in_(list(ids)))
    return {pid: q_unit_cost(amount / qty) for pid, amount, qty in session.execute(stmt) if qty}


# ---------------------------------------------------------------- რეცეპტი

def waste_multiplier(recipe: Recipe) -> Decimal:
    return Decimal(1) + (recipe.waste_percent or Decimal(0)) / Decimal(100)


@dataclass
class RecipeCostLine:
    line: RecipeLine
    consumption_base: Decimal  # ერთ პარტიაზე, ბრაკის ჩათვლით
    unit_cost: Decimal | None
    amount: Decimal | None


@dataclass
class RecipeCost:
    lines: list[RecipeCostLine] = field(default_factory=list)
    batch_total: Decimal = Decimal("0.00")
    per_unit: Decimal | None = None  # პროდუქტის ჩვენების ერთეულზე (მაგ. 1 ცალზე)
    missing: list[Ingredient] = field(default_factory=list)  # ფასი უცნობია (მიღება არ ყოფილა)


def recipe_cost(session: Session, recipe: Recipe, costs: dict[int, Decimal] | None = None) -> RecipeCost:
    if costs is None:
        costs = ingredient_costs(session, ids=[ln.ingredient_id for ln in recipe.lines])
    mult = waste_multiplier(recipe)
    result = RecipeCost()
    total = Decimal(0)
    for ln in recipe.lines:
        consumption = ln.qty_base * mult
        uc = costs.get(ln.ingredient_id)
        amount = None if uc is None else consumption * uc
        if uc is None:
            result.missing.append(ln.ingredient)
        else:
            total += amount
        result.lines.append(RecipeCostLine(ln, consumption, uc, None if amount is None else q_money(amount)))
    result.batch_total = q_money(total)
    if recipe.yield_qty and recipe.yield_qty > 0:
        units_per_batch = recipe.yield_qty / recipe.product.unit.factor
        result.per_unit = q_money(total / units_per_batch)
    return result
