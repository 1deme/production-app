"""წარმოება: მზა პროდუქციის გამოშვება რეცეპტით.

დადასტურება ერთ ტრანზაქციაში ხდება:
  1) ინგრედიენტები ჩამოიწერება (ფაქტობრივი რაოდენობით, რომელიც საბუთში ინახება);
  2) ინგრედიენტების ფასი „იყინება“ საბუთში (თვითღირებულება);
  3) მზა პროდუქცია ემატება საწყობს;
  4) მოწმდება ნაშთები. თუ რამე აკლია (და პარამეტრი კრძალავს) — არაფერი ჩაიწერება.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db_types import q_money, q_qty, q_unit_cost
from app.formatting import fmt_date, fmt_money, fmt_qty
from app.models.documents import DocStatus, ItemType, MovementType, ProductionLine, ProductionRun
from app.models.ingredients import Ingredient
from app.models.products import Product, Recipe
from app.models.system import User
from app.services import audit, stock
from app.services.costing import ingredient_costs
from app.services.documents import (allow_negative_ingredients, mark_confirmed, next_number, require_draft,
                                    require_not_cancelled)
from app.services.errors import BusinessError, ErrorBag
from app.services.i18n_labels import status_label
from app.services.recipes import consumption
from app.services.text import clean_name
from app.services.units import to_base

DOC = "production"
ZERO = Decimal("0.000")


@dataclass
class ProductionInput:
    date: date | None
    product_id: int | None
    qty: Decimal | None  # პროდუქტის ჩვენების ერთეულში
    note: str = ""
    # ფაქტობრივი ხარჯი: {ingredient_id: რაოდენობა ინგრედიენტის ჩვენების ერთეულში}. None — რეცეპტით.
    actual: dict[int, Decimal] | None = None


@dataclass
class PlanLine:
    ingredient: Ingredient
    planned_base: Decimal
    actual_base: Decimal
    available_base: Decimal = ZERO

    @property
    def shortage_base(self) -> Decimal:
        return max(self.actual_base - self.available_base, ZERO)

    @property
    def changed(self) -> bool:
        return self.actual_base != self.planned_base


@dataclass
class Plan:
    product: Product
    recipe: Recipe
    qty_base: Decimal
    lines: list[PlanLine] = field(default_factory=list)

    @property
    def has_shortage(self) -> bool:
        return any(ln.shortage_base > 0 for ln in self.lines)


def snapshot(r: ProductionRun) -> dict:
    return {
        "number": r.number,
        "date": fmt_date(r.date),
        "product": r.product.name,
        "qty": fmt_qty(r.qty_base, r.product.unit),
        "status": status_label(r.status),
        "note": r.note or "",
        "lines": "; ".join(f"{ln.ingredient.name} {fmt_qty(ln.qty_base, ln.ingredient.unit)}" for ln in r.lines),
        "total": fmt_money(r.total_cost),
    }


def build_plan(session: Session, product: Product, qty_base: Decimal, on: date | None,
               actual: dict[int, Decimal] | None = None, exclude_run_id: int | None = None,
               recipe: Recipe | None = None) -> Plan:
    """რა ჩამოიწერება და საკმარისია თუ არა ნაშთი. actual — ფაქტობრივი რაოდენობები (ჩვენების ერთეულში)."""
    recipe = recipe or product.active_recipe
    if recipe is None:
        raise BusinessError("production.no_recipe", name=product.name)
    plan = Plan(product=product, recipe=recipe, qty_base=qty_base)
    planned = consumption(recipe, qty_base) if qty_base > 0 else [(ln.ingredient, ZERO) for ln in recipe.lines]
    ids = [ing.id for ing, _ in planned]
    avail = stock.balances(session, ItemType.INGREDIENT, as_of=on, ids=ids,
                           exclude_doc=(DOC, exclude_run_id) if exclude_run_id else None)
    for ing, planned_base in planned:
        actual_base = planned_base
        if actual is not None and ing.id in actual and actual[ing.id] is not None:
            actual_base = to_base(actual[ing.id], ing.unit)
        plan.lines.append(PlanLine(ing, planned_base, actual_base, avail.get(ing.id, ZERO)))
    return plan


def _validate(session: Session, data: ProductionInput, existing: ProductionRun | None):
    errors = ErrorBag()
    if data.date is None:
        errors.add("date", "common.required")
    product = session.get(Product, data.product_id) if data.product_id else None
    if product is None:
        errors.add("product_id", "production.choose_product")
    elif not product.is_active and (existing is None or existing.product_id != product.id):
        errors.add("product_id", "docs.line_inactive", n=1, name=product.name)
    if data.qty is None or data.qty <= 0:
        errors.add("qty", "production.qty_positive")
    if data.actual:
        for ing_id, q in data.actual.items():
            if q is not None and q < 0:
                errors.add("lines", "errors.negative_not_allowed")
                break
    errors.raise_if_any()
    qty_base = to_base(data.qty, product.unit)
    if qty_base <= 0:
        raise BusinessError("production.qty_positive")
    # რედაქტირებისას იმავე პროდუქტზე ვინარჩუნებთ საბუთის რეცეპტს (ვერსიას)
    recipe = existing.recipe if existing and existing.product_id == product.id and existing.recipe else None
    plan = build_plan(session, product, qty_base, data.date, data.actual,
                      exclude_run_id=existing.id if existing else None, recipe=recipe)
    return product, plan


def _post(session: Session, run: ProductionRun) -> set:
    affected = stock.remove_doc(session, DOC, run.id)
    costs = ingredient_costs(session, as_of=run.date, ids=[ln.ingredient_id for ln in run.lines])
    total = Decimal(0)
    for ln in run.lines:
        ln.unit_cost = costs.get(ln.ingredient_id, Decimal(0))
        ln.amount = q_money(ln.qty_base * ln.unit_cost)
        total += ln.amount
        if ln.qty_base > 0:
            stock.add(session, item_type=ItemType.INGREDIENT, item_id=ln.ingredient_id, on=run.date,
                      qty=-ln.qty_base, movement_type=MovementType.PRODUCTION_USE, doc_type=DOC, doc_id=run.id,
                      doc_number=run.number, unit_cost=ln.unit_cost, amount=-ln.amount)
        affected.add((ItemType.INGREDIENT, ln.ingredient_id))
    run.total_cost = q_money(total)
    run.unit_cost = q_unit_cost(total / run.qty_base)
    stock.add(session, item_type=ItemType.PRODUCT, item_id=run.product_id, on=run.date, qty=run.qty_base,
              movement_type=MovementType.PRODUCTION_OUTPUT, doc_type=DOC, doc_id=run.id, doc_number=run.number,
              unit_cost=run.unit_cost, amount=run.total_cost)
    affected.add((ItemType.PRODUCT, run.product_id))
    return affected


def _check(session: Session, affected: set) -> None:
    stock.check_non_negative(session, affected, allow_negative_ingredients=allow_negative_ingredients(session))


def save_run(session: Session, data: ProductionInput, run: ProductionRun | None = None, *,
             confirm: bool = False, user: User | None = None) -> ProductionRun:
    if run is not None:
        require_not_cancelled(run)
    product, plan = _validate(session, data, run)
    is_new = run is None
    before = None if is_new else snapshot(run)
    old_items = set() if is_new else stock.doc_items(session, DOC, run.id)
    if is_new:
        run = ProductionRun(number=next_number(session, DOC, data.date), status=DocStatus.DRAFT)
        session.add(run)
    run.date = data.date
    run.product = product
    run.recipe = plan.recipe
    run.qty_base = plan.qty_base
    run.note = clean_name(data.note) or None
    run.lines.clear()
    session.flush()
    for pos, pl in enumerate(plan.lines):
        run.lines.append(ProductionLine(position=pos, ingredient=pl.ingredient, qty_base=q_qty(pl.actual_base),
                                        planned_qty_base=pl.planned_base))
    session.flush()
    if run.status == DocStatus.CONFIRMED or confirm:
        if run.status != DocStatus.CONFIRMED:
            mark_confirmed(run)
        _check(session, _post(session, run) | old_items)
    audit.log(session, action="create" if is_new else "update", entity=DOC, entity_id=run.id, label=run.number,
              before=before, after=snapshot(run), user=user)
    return run


def confirm_run(session: Session, run: ProductionRun, user: User | None = None) -> None:
    require_draft(run)
    before = snapshot(run)
    mark_confirmed(run)
    _check(session, _post(session, run))
    audit.log(session, action="confirm", entity=DOC, entity_id=run.id, label=run.number, before=before,
              after=snapshot(run), user=user)


def cancel_run(session: Session, run: ProductionRun, user: User | None = None) -> None:
    require_not_cancelled(run)
    before = snapshot(run)
    affected = stock.remove_doc(session, DOC, run.id)
    run.status = DocStatus.CANCELLED
    _check(session, affected)  # მაგ. გამოშვებული პროდუქცია უკვე გატანილია → გაუქმება იბლოკება
    audit.log(session, action="cancel", entity=DOC, entity_id=run.id, label=run.number, before=before,
              after=snapshot(run), user=user)


def delete_run(session: Session, run: ProductionRun, user: User | None = None) -> None:
    require_draft(run)
    before, rid, label = snapshot(run), run.id, run.number
    session.delete(run)
    session.flush()
    audit.log(session, action="delete", entity=DOC, entity_id=rid, label=label, before=before, user=user)
