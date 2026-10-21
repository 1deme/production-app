"""წარმოების საბუთები: გეგმა (რა ჩამოიწერება და საკმარისია თუ არა), დადასტურება, რედაქტირება, გაუქმება."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatting import fmt_date, input_value
from app.i18n import t
from app.models.documents import DocStatus, ProductionRun
from app.models.products import Product
from app.models.system import User
from app.services import audit
from app.services import production as svc
from app.services.documents import allow_negative_ingredients
from app.services.errors import BusinessError, ValidationError
from app.services.units import to_base
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_date, get_decimal, get_int, get_or_404, redirect
from app.web.routes.receipts import doc_list_filters
from app.web.templating import flash, render

router = APIRouter(prefix="/production")


@router.get("")
def list_runs(request: Request, q: str = "", status: str = "", date_from: str = "", date_to: str = "",
              db: Session = Depends(get_db)):
    conds = doc_list_filters(ProductionRun, q, status, date_from, date_to)
    stmt = select(ProductionRun).where(*conds).order_by(ProductionRun.date.desc(), ProductionRun.id.desc()).limit(300)
    return render(request, "production/list.html", {
        "active_nav": "production", "docs": list(db.scalars(stmt)), "q": q, "status": status,
        "date_from": date_from, "date_to": date_to,
    })


def _products(db):
    return list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.search_name)))


def _plan_key(product_id, qty) -> str:
    return f"{product_id}:{qty}"


async def _read(request, db):
    form = await request.form()
    errors = FormErrors()
    values = {k: str(form.get(k, "")) for k in ("date", "product_id", "qty", "note", "action", "plan_key")}
    product = db.get(Product, get_int(values["product_id"]))
    qty = get_decimal(values["qty"], errors, "qty", places=3, label=t("production.qty"))
    actual = None
    # ფაქტობრივი რაოდენობები ითვალისწინება მხოლოდ მაშინ, თუ ისინი ამავე პროდუქტისა და რაოდენობის გეგმას ეკუთვნის
    if product and qty is not None and values["plan_key"] == _plan_key(product.id, qty):
        actual = {}
        for key, raw in form.multi_items():
            if key.startswith("actual_"):
                ing_id = get_int(key[len("actual_"):])
                val = get_decimal(raw, errors, "lines", places=3, label=t("production.actual"))
                if ing_id and val is not None:
                    actual[ing_id] = val
    data = svc.ProductionInput(date=get_date(values["date"], errors), product_id=product.id if product else None,
                               qty=qty, note=values["note"], actual=actual)
    return values, data, errors


def _plan_ctx(db, data: svc.ProductionInput, run: ProductionRun | None, values: dict):
    """გეგმის ნაწილი (ცხრილი + ღილაკები)."""
    ctx = {"plan": None, "plan_error": None, "run": run, "values": values,
           "warn_mode": allow_negative_ingredients(db), "plan_key": ""}
    product = db.get(Product, data.product_id) if data.product_id else None
    if product is None:
        return ctx
    if data.qty is None or data.qty <= 0:
        ctx["plan_error"] = t("production.enter_qty")
        return ctx
    try:
        recipe = run.recipe if run and run.product_id == product.id and run.recipe else None
        ctx["plan"] = svc.build_plan(db, product, to_base(data.qty, product.unit), data.date or date.today(),
                                     data.actual, exclude_run_id=run.id if run else None, recipe=recipe)
        ctx["plan_key"] = _plan_key(product.id, data.qty)
    except BusinessError as e:
        ctx["plan_error"] = e.msg.render()
    return ctx


def _form(request, db, run, values, data, errors=None, status_code=200):
    ctx = {"active_nav": "production", "run": run, "values": values, "errors": errors or FormErrors(),
           "products": _products(db)}
    ctx.update(_plan_ctx(db, data, run, values))
    return render(request, "production/form.html", ctx, status_code)


@router.get("/new")
def new_form(request: Request, product_id: int | None = None, db: Session = Depends(get_db)):
    values = {"date": fmt_date(date.today()), "product_id": str(product_id or ""), "qty": "", "note": ""}
    data = svc.ProductionInput(date=date.today(), product_id=product_id, qty=None)
    return _form(request, db, None, values, data)


@router.post("/plan")
async def plan(request: Request, db: Session = Depends(get_db)):
    values, data, _ = await _read(request, db)
    run_id = get_int((await request.form()).get("run_id"))  # ფორმა ქეშირებულია — მეორედ არ იკითხება
    run = db.get(ProductionRun, run_id) if run_id else None
    return render(request, "production/_plan.html", _plan_ctx(db, data, run, values))


async def _save(request, db, user, run=None):
    values, data, errors = await _read(request, db)
    if not errors:
        try:
            r = svc.save_run(db, data, run, confirm=values["action"] == "confirm", user=user)
            db.commit()
            key = "production.confirmed" if values["action"] == "confirm" else "production.saved"
            flash(request, t(key, number=r.number))
            return redirect(f"/production/{r.id}")
        except ValidationError as e:
            db.rollback()
            errors.merge(e)
        except BusinessError as e:
            db.rollback()
            errors.add("_doc", e.msg.render())
    current = db.get(ProductionRun, run.id) if run else None
    return _form(request, db, current, values, data, errors, 422)


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    return await _save(request, db, user)


@router.get("/{rid:int}")
def detail(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, ProductionRun, rid)
    return render(request, "production/detail.html", {
        "active_nav": "production", "doc": r, "audit_entries": audit.entries_for(db, svc.DOC, r.id),
        "parse_changes": audit.parsed_changes,
    })


@router.get("/{rid:int}/edit")
def edit_form(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, ProductionRun, rid)
    if r.status == DocStatus.CANCELLED:
        flash(request, t("docs.cancelled_readonly"), "warning")
        return redirect(f"/production/{rid}")
    qty = r.qty_base / r.product.unit.factor
    values = {"date": fmt_date(r.date), "product_id": str(r.product_id), "qty": input_value(qty), "note": r.note or ""}
    actual = {ln.ingredient_id: ln.qty_base / ln.ingredient.unit.factor for ln in r.lines}
    data = svc.ProductionInput(date=r.date, product_id=r.product_id, qty=qty, actual=actual)
    return _form(request, db, r, values, data)


@router.post("/{rid:int}/edit")
async def update(request: Request, rid: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    return await _save(request, db, user, get_or_404(db, ProductionRun, rid))


@router.post("/{rid:int}/{action}")
def action(request: Request, rid: int, action: str, db: Session = Depends(get_db),
           user: User | None = Depends(get_current_user)):
    r = get_or_404(db, ProductionRun, rid)
    number = r.number
    handlers = {"confirm": (svc.confirm_run, "production.confirmed"),
                "cancel": (svc.cancel_run, "production.cancelled"),
                "delete": (svc.delete_run, "production.deleted")}
    if action not in handlers:
        return redirect(f"/production/{rid}")
    fn, msg = handlers[action]
    try:
        fn(db, r, user)
        db.commit()
    except (BusinessError, ValidationError) as e:
        db.rollback()
        text = e.msg.render() if isinstance(e, BusinessError) else " ".join(sum(e.rendered().values(), []))
        flash(request, text, "danger")
        return redirect(f"/production/{rid}")
    flash(request, t(msg, number=number), "success" if action == "confirm" else "warning")
    return redirect("/production" if action == "delete" else f"/production/{rid}")
