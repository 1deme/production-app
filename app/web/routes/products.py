"""მზა პროდუქცია: კატალოგი, ნაშთი, მოძრაობის ისტორია."""
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.i18n import t
from app.models.documents import ItemType
from app.models.products import Product
from app.models.system import User
from app.services import audit, costing, stock
from app.services import products as svc
from app.services.errors import BusinessError, ValidationError
from app.services.units import all_units
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_int, get_or_404, redirect
from app.web.templating import flash, render

router = APIRouter(prefix="/products")


@router.get("")
def list_products(request: Request, q: str = "", status: str = "active", db: Session = Depends(get_db)):
    if status not in ("active", "inactive", "all"):
        status = "active"
    items = svc.search_products(db, q, status)
    ids = [p.id for p in items]
    bal = stock.balances(db, ItemType.PRODUCT, ids=ids)
    ing_costs = costing.ingredient_costs(db)
    recipe_costs = {p.id: costing.recipe_cost(db, p.active_recipe, ing_costs) for p in items if p.active_recipe}
    return render(request, "products/list.html", {
        "active_nav": "products", "items": items, "q": q, "status": status, "bal": bal,
        "recipe_costs": recipe_costs, "zero": Decimal(0),
    })


def _ctx(db, product, values, errors=None):
    return {"active_nav": "products", "product": product, "values": values, "errors": errors or FormErrors(),
            "units": all_units(db), "kind_locked": bool(product and svc.is_product_used(db, product.id))}


async def _read(request):
    form = await request.form()
    values = {k: str(form.get(k, "")) for k in ("name", "code", "barcode", "unit_id", "note")}
    data = svc.ProductInput(name=values["name"], unit_id=get_int(values["unit_id"]), code=values["code"],
                            barcode=values["barcode"], note=values["note"])
    return values, data


@router.get("/new")
def new_form(request: Request, db: Session = Depends(get_db)):
    pc = next((u for u in all_units(db) if u.code == "pc"), None)
    values = {"name": "", "code": "", "barcode": "", "unit_id": str(pc.id if pc else ""), "note": ""}
    return render(request, "products/form.html", _ctx(db, None, values))


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    values, data = await _read(request)
    try:
        p = svc.create_product(db, data, user)
        db.commit()
    except ValidationError as e:
        db.rollback()
        return render(request, "products/form.html", _ctx(db, None, values, FormErrors().merge(e)), 422)
    flash(request, t("products.created", name=p.name))
    return redirect(f"/products/{p.id}")


@router.get("/{pid:int}")
def detail(request: Request, pid: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, pid)
    rc = costing.recipe_cost(db, p.active_recipe) if p.active_recipe else None
    return render(request, "products/detail.html", {
        "active_nav": "products", "p": p, "rc": rc,
        "balance": stock.balance(db, ItemType.PRODUCT, p.id),
        "avg_cost": costing.product_costs(db, ids=[p.id]).get(p.id),
        "history": stock.history(db, ItemType.PRODUCT, p.id),
        "audit_entries": audit.entries_for(db, svc.ENTITY, p.id),
        "parse_changes": audit.parsed_changes,
        "used": svc.is_product_used(db, p.id),
    })


@router.get("/{pid:int}/edit")
def edit_form(request: Request, pid: int, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, pid)
    values = {"name": p.name, "code": p.code or "", "barcode": p.barcode or "", "unit_id": str(p.unit_id),
              "note": p.note or ""}
    return render(request, "products/form.html", _ctx(db, p, values))


@router.post("/{pid:int}/edit")
async def update(request: Request, pid: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    p = get_or_404(db, Product, pid)
    values, data = await _read(request)
    try:
        svc.update_product(db, p, data, user)
        db.commit()
    except ValidationError as e:
        db.rollback()
        p = get_or_404(db, Product, pid)
        return render(request, "products/form.html", _ctx(db, p, values, FormErrors().merge(e)), 422)
    flash(request, t("products.updated", name=p.name))
    return redirect(f"/products/{p.id}")


@router.post("/{pid:int}/{action}")
def change_status(request: Request, pid: int, action: str, db: Session = Depends(get_db),
                  user: User | None = Depends(get_current_user)):
    p = get_or_404(db, Product, pid)
    name = p.name
    try:
        if action == "deactivate":
            svc.set_active(db, p, False, user)
            msg, level = t("products.deactivated", name=name), "warning"
        elif action == "activate":
            svc.set_active(db, p, True, user)
            msg, level = t("products.activated", name=name), "success"
        elif action == "delete":
            svc.delete_product(db, p, user)
            db.commit()
            flash(request, t("products.deleted", name=name))
            return redirect("/products")
        else:
            return redirect(f"/products/{pid}")
        db.commit()
        flash(request, msg, level)
    except BusinessError as e:
        db.rollback()
        flash(request, e.msg.render(), "danger")
    return redirect(f"/products/{pid}")
