"""რეპორტების მონაცემები.

ყველა რეპორტი აბრუნებს ერთნაირ სტრუქტურას (Report → Section → სვეტები/სტრიქონები),
რომელსაც ერთნაირად ხატავს HTML, Excel და PDF. რიცხვები Decimal-ად რჩება ბოლომდე.
მხედველობაში მიიღება მხოლოდ დადასტურებული საბუთები (ანუ მოძრაობები).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db_types import q_money, q_price
from app.formatting import fmt_date
from app.i18n import t
from app.models.documents import (Dispatch, DispatchLine, DocStatus, ItemType, MovementType, ProductionLine,
                                  ProductionRun, Receipt, StockMovement)
from app.models.ingredients import Ingredient
from app.models.products import Product
from app.services import costing, stock

ZERO = Decimal(0)


@dataclass
class Column:
    key: str
    label: str
    kind: str = "text"  # text | qty | money | num | date | percent


@dataclass
class Section:
    title: str
    columns: list[Column]
    rows: list[dict] = field(default_factory=list)
    totals: dict | None = None
    row_flags: list[str] = field(default_factory=list)  # მაგ. "low" — წითლად
    note: str = ""


@dataclass
class Report:
    key: str
    title: str
    period: str
    sections: list[Section]
    needs_period: bool = True


def _period(start: date, end: date) -> str:
    return t("reports.period", start=fmt_date(start), end=fmt_date(end))


def _disp(qty_base, unit) -> Decimal:
    return (qty_base or ZERO) / unit.factor


# ---------------------------------------------------------------- 1. ინგრედიენტების ნაშთები

def ingredient_stock(session: Session, as_of: date) -> Report:
    items = list(session.scalars(select(Ingredient).where(Ingredient.is_active.is_(True)).order_by(Ingredient.search_name)))
    bal = stock.balances(session, ItemType.INGREDIENT, as_of=as_of)
    costs = costing.ingredient_costs(session, as_of=as_of)
    sec = Section(t("reports.ingredient_stock"), [
        Column("name", t("ingredients.name")), Column("unit", t("docs.unit")),
        Column("stock", t("reports.stock"), "qty"), Column("min", t("ingredients.min_stock"), "qty"),
        Column("cost", t("reports.unit_cost"), "price"), Column("value", t("reports.value"), "money"),
    ], note=t("reports.low_note"))
    total = ZERO
    for ing in items:
        b = bal.get(ing.id, ZERO)
        uc = costs.get(ing.id)
        value = q_money(b * uc) if uc is not None else None
        total += value or ZERO
        sec.rows.append({"name": ing.name, "unit": ing.unit.name, "stock": _disp(b, ing.unit),
                         "min": _disp(ing.min_stock, ing.unit) if ing.min_stock else None,
                         "cost": q_price(uc * ing.unit.factor) if uc is not None else None, "value": value})
        sec.row_flags.append("low" if (ing.min_stock > 0 and b < ing.min_stock) or b < 0 else "")
    sec.totals = {"name": t("docs.total"), "value": q_money(total)}
    return Report("ingredient_stock", t("reports.ingredient_stock"), t("reports.as_of", date=fmt_date(as_of)),
                  [sec], needs_period=False)


# ---------------------------------------------------------------- 2. მზა პროდუქციის ნაშთები

def product_stock(session: Session, as_of: date) -> Report:
    items = list(session.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.search_name)))
    bal = stock.balances(session, ItemType.PRODUCT, as_of=as_of)
    costs = costing.product_costs(session, as_of=as_of)
    sec = Section(t("reports.product_stock"), [
        Column("name", t("products.name")), Column("unit", t("docs.unit")),
        Column("stock", t("reports.stock"), "qty"), Column("cost", t("reports.unit_cost"), "price"),
        Column("value", t("reports.value"), "money"),
    ])
    total = ZERO
    for p in items:
        b = bal.get(p.id, ZERO)
        uc = costs.get(p.id)
        value = q_money(b * uc) if uc is not None else None
        total += value or ZERO
        sec.rows.append({"name": p.name, "unit": p.unit.name, "stock": _disp(b, p.unit),
                         "cost": q_price(uc * p.unit.factor) if uc is not None else None, "value": value})
        sec.row_flags.append("low" if b < 0 else "")
    sec.totals = {"name": t("docs.total"), "value": q_money(total)}
    return Report("product_stock", t("reports.product_stock"), t("reports.as_of", date=fmt_date(as_of)),
                  [sec], needs_period=False)


# ---------------------------------------------------------------- 3. მოძრაობა პერიოდში

def movement(session: Session, start: date, end: date) -> Report:
    sections = []
    # ინგრედიენტები
    data = stock.period_summary(session, ItemType.INGREDIENT, start, end)
    sec = Section(t("nav.ingredients"), [
        Column("name", t("ingredients.name")), Column("unit", t("docs.unit")),
        Column("opening", t("reports.opening"), "qty"), Column("in", t("reports.received"), "qty"),
        Column("out", t("reports.consumed"), "qty"), Column("adj", t("reports.adjusted"), "qty"),
        Column("closing", t("reports.closing"), "qty"),
    ])
    ings = {i.id: i for i in session.scalars(select(Ingredient).where(Ingredient.id.in_(list(data))))}
    for iid, row in sorted(data.items(), key=lambda kv: ings[kv[0]].search_name):
        u = ings[iid].unit
        sec.rows.append({"name": ings[iid].name, "unit": u.name, "opening": _disp(row["opening"], u),
                         "in": _disp(row["receipt"], u), "out": _disp(-row["production_use"], u),
                         "adj": _disp(row["adjustment"], u), "closing": _disp(row["closing"], u)})
        sec.row_flags.append("low" if row["closing"] < 0 else "")
    sections.append(sec)
    # პროდუქცია
    data = stock.period_summary(session, ItemType.PRODUCT, start, end)
    sec = Section(t("nav.products"), [
        Column("name", t("products.name")), Column("unit", t("docs.unit")),
        Column("opening", t("reports.opening"), "qty"), Column("in", t("reports.produced"), "qty"),
        Column("out", t("reports.dispatched"), "qty"), Column("adj", t("reports.adjusted"), "qty"),
        Column("closing", t("reports.closing"), "qty"),
    ])
    prods = {p.id: p for p in session.scalars(select(Product).where(Product.id.in_(list(data))))}
    for pid, row in sorted(data.items(), key=lambda kv: prods[kv[0]].search_name):
        u = prods[pid].unit
        sec.rows.append({"name": prods[pid].name, "unit": u.name, "opening": _disp(row["opening"], u),
                         "in": _disp(row["production_output"], u), "out": _disp(-row["dispatch"], u),
                         "adj": _disp(row["adjustment"], u), "closing": _disp(row["closing"], u)})
        sec.row_flags.append("low" if row["closing"] < 0 else "")
    sections.append(sec)
    return Report("movement", t("reports.movement"), _period(start, end), sections)


# ---------------------------------------------------------------- 4. წარმოების რეპორტი

def _runs(session: Session, start: date, end: date) -> list[ProductionRun]:
    return list(session.scalars(select(ProductionRun).where(
        ProductionRun.status == DocStatus.CONFIRMED, ProductionRun.date >= start, ProductionRun.date <= end)
        .order_by(ProductionRun.date, ProductionRun.id)))


def production_report(session: Session, start: date, end: date) -> Report:
    runs = _runs(session, start, end)
    docs = Section(t("reports.production_docs"), [
        Column("number", t("docs.number")), Column("date", t("common.date"), "date"),
        Column("product", t("recipes.product")), Column("qty", t("production.qty"), "qty"),
        Column("unit", t("docs.unit")), Column("cost", t("production.total_cost"), "money"),
        Column("unit_cost", t("reports.unit_cost"), "price"),
    ])
    by_product: dict[int, list] = defaultdict(lambda: [None, ZERO, ZERO])
    by_ing: dict[int, list] = defaultdict(lambda: [None, ZERO, ZERO])
    for r in runs:
        u = r.product.unit
        docs.rows.append({"number": r.number, "date": r.date, "product": r.product.name, "qty": _disp(r.qty_base, u),
                          "unit": u.name, "cost": r.total_cost, "unit_cost": q_price(r.unit_cost * u.factor)})
        bp = by_product[r.product_id]
        bp[0], bp[1], bp[2] = r.product, bp[1] + r.qty_base, bp[2] + r.total_cost
        for ln in r.lines:
            bi = by_ing[ln.ingredient_id]
            bi[0], bi[1], bi[2] = ln.ingredient, bi[1] + ln.qty_base, bi[2] + ln.amount
    docs.totals = {"number": t("docs.total"), "cost": q_money(sum((r.total_cost for r in runs), ZERO))}

    prod = Section(t("reports.produced_by_product"), [
        Column("product", t("recipes.product")), Column("qty", t("production.qty"), "qty"),
        Column("unit", t("docs.unit")), Column("cost", t("production.total_cost"), "money"),
        Column("unit_cost", t("reports.avg_unit_cost"), "price"),
    ])
    for p, qty, cost in sorted(by_product.values(), key=lambda x: x[0].search_name):
        prod.rows.append({"product": p.name, "qty": _disp(qty, p.unit), "unit": p.unit.name, "cost": q_money(cost),
                          "unit_cost": q_price(cost / (qty / p.unit.factor)) if qty else None})
    ing = Section(t("reports.consumed_ingredients"), [
        Column("name", t("ingredients.name")), Column("qty", t("docs.qty"), "qty"),
        Column("unit", t("docs.unit")), Column("cost", t("recipes.cost"), "money"),
    ])
    for i, qty, cost in sorted(by_ing.values(), key=lambda x: x[0].search_name):
        ing.rows.append({"name": i.name, "qty": _disp(qty, i.unit), "unit": i.unit.name, "cost": q_money(cost)})
    ing.totals = {"name": t("docs.total"), "cost": q_money(sum((x[2] for x in by_ing.values()), ZERO))}
    return Report("production", t("reports.production"), _period(start, end), [docs, prod, ing])


# ---------------------------------------------------------------- 5. ხარჯი და თვითღირებულება პროდუქტზე

def cost_report(session: Session, start: date, end: date) -> Report:
    runs = _runs(session, start, end)
    agg: dict[int, dict] = {}
    for r in runs:
        a = agg.setdefault(r.product_id, {"product": r.product, "qty": ZERO, "cost": ZERO, "ings": defaultdict(lambda: [None, ZERO, ZERO])})
        a["qty"] += r.qty_base
        a["cost"] += r.total_cost
        for ln in r.lines:
            x = a["ings"][ln.ingredient_id]
            x[0], x[1], x[2] = ln.ingredient, x[1] + ln.qty_base, x[2] + ln.amount
    summary = Section(t("reports.cost_summary"), [
        Column("product", t("recipes.product")), Column("qty", t("reports.produced"), "qty"),
        Column("unit", t("docs.unit")), Column("cost", t("production.total_cost"), "money"),
        Column("unit_cost", t("reports.avg_unit_cost"), "price"),
    ])
    detail = Section(t("reports.cost_detail"), [
        Column("product", t("recipes.product")), Column("ingredient", t("ingredients.name")),
        Column("qty", t("docs.qty"), "qty"), Column("unit", t("docs.unit")),
        Column("per_unit", t("reports.per_product_unit"), "qty"),
        Column("cost", t("recipes.cost"), "money"), Column("share", t("reports.share"), "percent"),
    ])
    for a in sorted(agg.values(), key=lambda x: x["product"].search_name):
        p = a["product"]
        units = a["qty"] / p.unit.factor
        summary.rows.append({"product": p.name, "qty": units, "unit": p.unit.name, "cost": q_money(a["cost"]),
                             "unit_cost": q_price(a["cost"] / units) if units else None})
        for ing, qty, cost in sorted(a["ings"].values(), key=lambda x: -x[2]):
            detail.rows.append({
                "product": p.name, "ingredient": ing.name, "qty": _disp(qty, ing.unit), "unit": ing.unit.name,
                "per_unit": _disp(qty, ing.unit) / units if units else None, "cost": q_money(cost),
                "share": (cost / a["cost"] * 100) if a["cost"] else None,
            })
    return Report("cost", t("reports.cost"), _period(start, end), [summary, detail])


# ---------------------------------------------------------------- 6. გატანების რეპორტი

def dispatch_report(session: Session, start: date, end: date) -> Report:
    docs = list(session.scalars(select(Dispatch).where(
        Dispatch.status == DocStatus.CONFIRMED, Dispatch.date >= start, Dispatch.date <= end)
        .order_by(Dispatch.date, Dispatch.id)))
    lines = Section(t("reports.dispatch_lines"), [
        Column("number", t("docs.number")), Column("date", t("common.date"), "date"),
        Column("customer", t("dispatch.customer")), Column("product", t("recipes.product")),
        Column("qty", t("docs.qty"), "qty"), Column("unit", t("docs.unit")),
        Column("price", t("dispatch.price"), "price"), Column("amount", t("receipts.amount"), "money"),
    ])
    by_p: dict[int, list] = defaultdict(lambda: [None, ZERO, ZERO])
    by_c: dict[str, list] = defaultdict(lambda: [0, ZERO])
    for d in docs:
        cname = d.counterparty.name if d.counterparty else t("reports.no_customer")
        by_c[cname][0] += 1
        by_c[cname][1] += d.total
        for ln in d.lines:
            u = ln.product.unit
            lines.rows.append({"number": d.number, "date": d.date, "customer": cname, "product": ln.product.name,
                               "qty": _disp(ln.qty_base, u), "unit": u.name, "price": ln.unit_price,
                               "amount": ln.amount})
            x = by_p[ln.product_id]
            x[0], x[1], x[2] = ln.product, x[1] + ln.qty_base, x[2] + (ln.amount or ZERO)
    total = q_money(sum((d.total for d in docs), ZERO))
    lines.totals = {"number": t("docs.total"), "amount": total}
    prod = Section(t("reports.by_product"), [
        Column("product", t("recipes.product")), Column("qty", t("docs.qty"), "qty"),
        Column("unit", t("docs.unit")), Column("amount", t("receipts.amount"), "money"),
    ])
    for p, qty, amount in sorted(by_p.values(), key=lambda x: x[0].search_name):
        prod.rows.append({"product": p.name, "qty": _disp(qty, p.unit), "unit": p.unit.name, "amount": q_money(amount)})
    prod.totals = {"product": t("docs.total"), "amount": total}
    cust = Section(t("reports.by_customer"), [
        Column("customer", t("dispatch.customer")), Column("count", t("reports.doc_count"), "num"),
        Column("amount", t("receipts.amount"), "money"),
    ])
    for name, (count, amount) in sorted(by_c.items()):
        cust.rows.append({"customer": name, "count": Decimal(count), "amount": q_money(amount)})
    cust.totals = {"customer": t("docs.total"), "amount": total}
    return Report("dispatch", t("reports.dispatch"), _period(start, end), [lines, prod, cust])


REPORTS = {
    "ingredient_stock": (ingredient_stock, "bi-basket"),
    "product_stock": (product_stock, "bi-box-seam"),
    "movement": (movement, "bi-arrow-left-right"),
    "production": (production_report, "bi-gear-wide-connected"),
    "cost": (cost_report, "bi-calculator"),
    "dispatch": (dispatch_report, "bi-send"),
}


def build(session: Session, key: str, start: date, end: date) -> Report:
    fn, _ = REPORTS[key]
    if key in ("ingredient_stock", "product_stock"):
        return fn(session, end)
    return fn(session, start, end)


# ---------------------------------------------------------------- მთავარი გვერდი

def dashboard(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    start = today - timedelta(days=29)
    ings = list(session.scalars(select(Ingredient).where(Ingredient.is_active.is_(True))))
    bal = stock.balances(session, ItemType.INGREDIENT)
    low = sorted([(i, bal.get(i.id, ZERO)) for i in ings
                  if (i.min_stock > 0 and bal.get(i.id, ZERO) < i.min_stock) or bal.get(i.id, ZERO) < 0],
                 key=lambda x: x[0].search_name)
    prods = list(session.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.search_name)))
    pbal = stock.balances(session, ItemType.PRODUCT)

    days = [start + timedelta(days=i) for i in range(30)]
    prod_cost = defaultdict(lambda: ZERO)
    for d, amount in session.execute(select(StockMovement.date, func.sum(StockMovement.amount)).where(
            StockMovement.movement_type == MovementType.PRODUCTION_OUTPUT, StockMovement.date >= start)
            .group_by(StockMovement.date)):
        prod_cost[d] = amount or ZERO
    sales = defaultdict(lambda: ZERO)
    for d, amount in session.execute(select(Dispatch.date, func.sum(DispatchLine.amount)).join(DispatchLine).where(
            Dispatch.status == DocStatus.CONFIRMED, Dispatch.date >= start).group_by(Dispatch.date)):
        sales[d] = amount or ZERO
    top = session.execute(select(Ingredient.name, func.sum(ProductionLine.amount).label("cost"))
                          .join(ProductionLine, ProductionLine.ingredient_id == Ingredient.id)
                          .join(ProductionRun, ProductionRun.id == ProductionLine.run_id)
                          .where(ProductionRun.status == DocStatus.CONFIRMED, ProductionRun.date >= start)
                          .group_by(Ingredient.id).order_by(func.sum(ProductionLine.amount).desc()).limit(8)).all()

    def recent(model, kind, n=5):
        return [(kind, d) for d in session.scalars(select(model).order_by(model.id.desc()).limit(n))]
    docs = recent(Receipt, "receipt") + recent(ProductionRun, "production") + recent(Dispatch, "dispatch")
    docs.sort(key=lambda x: x[1].updated_at, reverse=True)
    drafts = sum(session.scalar(select(func.count(m.id)).where(m.status == DocStatus.DRAFT)) or 0
                 for m in (Receipt, ProductionRun, Dispatch))
    return {
        "low": low,
        "products": [(p, pbal.get(p.id, ZERO)) for p in prods],
        "chart_days": [d.strftime("%d.%m") for d in days],
        "chart_production": [float(prod_cost[d]) for d in days],
        "chart_sales": [float(sales[d]) for d in days],
        "top_ings": [(name, float(cost or 0)) for name, cost in top],
        "recent_docs": docs[:10],
        "drafts": drafts,
        "month_production": sum((prod_cost[d] for d in days), ZERO),
        "month_sales": sum((sales[d] for d in days), ZERO),
    }
