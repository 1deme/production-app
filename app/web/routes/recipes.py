"""რეცეპტები: რედაქტორი, ვერსიები, კოპირება, თვითღირებულება."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatting import input_value
from app.i18n import t
from app.models.ingredients import Ingredient
from app.models.products import Product, Recipe
from app.models.system import User
from app.services import audit, costing
from app.services import recipes as svc
from app.services.errors import BusinessError, ValidationError
from app.services.units import all_units
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_decimal, get_int, get_or_404, line_label, redirect
from app.web.templating import flash, render

router = APIRouter(prefix="/recipes")


def units_by_kind(db):
    out = {}
    for u in all_units(db):
        out.setdefault(u.kind, []).append(u)
    return out


@router.get("")
def list_recipes(request: Request, db: Session = Depends(get_db)):
    prods = list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.search_name)))
    costs = costing.ingredient_costs(db)
    rows = [(p, p.active_recipe, costing.recipe_cost(db, p.active_recipe, costs) if p.active_recipe else None)
            for p in prods]
    return render(request, "recipes/list.html", {"active_nav": "recipes", "rows": rows})


@router.get("/line-row")
def line_row(request: Request, id: int, db: Session = Depends(get_db)):
    ing = get_or_404(db, Ingredient, id)
    costs = costing.ingredient_costs(db, ids=[ing.id])
    return render(request, "recipes/_line.html", {
        "ln": {"ingredient": ing, "qty": "", "unit_id": ing.unit_id}, "cost": costs.get(ing.id),
        "ubk": units_by_kind(db), "errors": FormErrors(), "idx": 0,
    })


def _form_ctx(db, *, product, recipe, values, lines, errors=None):
    costs = costing.ingredient_costs(db, ids=[ln["ingredient"].id for ln in lines])
    return {
        "active_nav": "recipes", "product": product, "recipe": recipe, "values": values, "lines": lines,
        "costs": costs, "ubk": units_by_kind(db), "errors": errors or FormErrors(), "units": all_units(db),
        "products": list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.search_name))),
    }


async def _read(request, db):
    form = await request.form()
    errors = FormErrors()
    values = {k: str(form.get(k, "")) for k in ("yield_qty", "waste_percent", "note", "product_id")}
    ids, qtys, unit_ids = form.getlist("line_ingredient_id"), form.getlist("line_qty"), form.getlist("line_unit_id")
    lines_in, lines_view = [], []
    for i, (iid, q, uid) in enumerate(zip(ids, qtys, unit_ids), start=1):
        ing = db.get(Ingredient, get_int(iid))
        qty = get_decimal(q, errors, f"line:{i}", places=3, label=line_label(i))
        lines_in.append(svc.RecipeLineInput(get_int(iid), qty, get_int(uid)))
        if ing:
            lines_view.append({"ingredient": ing, "qty": str(q), "unit_id": get_int(uid)})
    data = svc.RecipeInput(
        yield_qty=get_decimal(values["yield_qty"], errors, "yield_qty", places=3, label=t("recipes.yield")),
        waste_percent=get_decimal(values["waste_percent"] or "0", errors, "waste_percent", places=2,
                                  label=t("recipes.waste")),
        note=values["note"], lines=lines_in)
    return values, data, lines_view, errors


@router.get("/new")
def new_form(request: Request, product_id: int | None = None, db: Session = Depends(get_db)):
    product = db.get(Product, product_id) if product_id else None
    if product and product.active_recipe:
        flash(request, t("recipes.already_has_recipe", name=product.name), "info")
        return redirect(f"/recipes/{product.active_recipe.id}")
    values = {"yield_qty": "1", "waste_percent": "0", "note": "", "product_id": str(product.id) if product else ""}
    return render(request, "recipes/form.html", _form_ctx(db, product=product, recipe=None, values=values, lines=[]))


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    values, data, lines, errors = await _read(request, db)
    product = db.get(Product, get_int(values["product_id"]))
    if product is None:
        errors.add("product_id", t("recipes.choose_product"))
    if not errors:
        try:
            r = svc.create_recipe(db, product, data, user)
            db.commit()
            flash(request, t("recipes.created", name=product.name, v=r.version))
            return redirect(f"/recipes/{r.id}")
        except ValidationError as e:
            db.rollback()
            errors.merge(e)
    return render(request, "recipes/form.html",
                  _form_ctx(db, product=product, recipe=None, values=values, lines=lines, errors=errors), 422)


@router.get("/{rid:int}")
def detail(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, Recipe, rid)
    others = list(db.scalars(select(Product).where(Product.is_active.is_(True), Product.id != r.product_id)
                             .order_by(Product.search_name)))
    return render(request, "recipes/detail.html", {
        "active_nav": "recipes", "r": r, "rc": costing.recipe_cost(db, r), "used": svc.is_recipe_used(db, r.id),
        "other_products": others, "cost_method": costing.cost_method(db),
        "audit_entries": audit.entries_for(db, svc.ENTITY, r.id), "parse_changes": audit.parsed_changes,
    })


@router.get("/{rid:int}/edit")
def edit_form(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, Recipe, rid)
    if svc.is_recipe_used(db, r.id):
        flash(request, t("recipes.locked_used"), "warning")
        return redirect(f"/recipes/{r.id}")
    values = {"yield_qty": input_value(r.yield_qty / r.product.unit.factor), "waste_percent": input_value(r.waste_percent),
              "note": r.note or "", "product_id": str(r.product_id)}
    lines = [{"ingredient": ln.ingredient, "qty": input_value(ln.qty), "unit_id": ln.unit_id} for ln in r.lines]
    return render(request, "recipes/form.html", _form_ctx(db, product=r.product, recipe=r, values=values, lines=lines))


@router.post("/{rid:int}/edit")
async def update(request: Request, rid: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Recipe, rid)
    values, data, lines, errors = await _read(request, db)
    if not errors:
        try:
            svc.update_recipe(db, r, data, user)
            db.commit()
            flash(request, t("recipes.updated"))
            return redirect(f"/recipes/{r.id}")
        except (ValidationError, BusinessError) as e:
            db.rollback()
            r = get_or_404(db, Recipe, rid)
            if isinstance(e, ValidationError):
                errors.merge(e)
            else:
                errors.add("_doc", e.msg.render())
    return render(request, "recipes/form.html",
                  _form_ctx(db, product=r.product, recipe=r, values=values, lines=lines, errors=errors), 422)


@router.post("/{rid:int}/new-version")
def new_version(request: Request, rid: int, db: Session = Depends(get_db),
                user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Recipe, rid)
    copy = svc.new_version(db, r, user)
    db.commit()
    flash(request, t("recipes.version_created", v=copy.version))
    return redirect(f"/recipes/{copy.id}/edit")


@router.post("/{rid:int}/activate")
def activate(request: Request, rid: int, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Recipe, rid)
    svc.activate(db, r, user)
    db.commit()
    flash(request, t("recipes.activated", v=r.version))
    return redirect(f"/recipes/{rid}")


@router.post("/{rid:int}/copy")
async def copy(request: Request, rid: int, db: Session = Depends(get_db),
               user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Recipe, rid)
    form = await request.form()
    target = db.get(Product, get_int(form.get("target_product_id")))
    if target is None:
        flash(request, t("recipes.choose_product"), "danger")
        return redirect(f"/recipes/{rid}")
    try:
        new = svc.copy_to_product(db, r, target, user)
        db.commit()
    except (BusinessError, ValidationError) as e:
        db.rollback()
        flash(request, e.msg.render() if isinstance(e, BusinessError) else str(e), "danger")
        return redirect(f"/recipes/{rid}")
    flash(request, t("recipes.copied", name=target.name, v=new.version))
    return redirect(f"/recipes/{new.id}")


@router.post("/{rid:int}/delete")
def delete(request: Request, rid: int, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Recipe, rid)
    pid = r.product_id
    try:
        svc.delete_recipe(db, r, user)
        db.commit()
    except BusinessError as e:
        db.rollback()
        flash(request, e.msg.render(), "danger")
        return redirect(f"/recipes/{rid}")
    flash(request, t("recipes.deleted"))
    return redirect(f"/products/{pid}")
