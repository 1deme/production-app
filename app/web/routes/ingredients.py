"""ინგრედიენტების გვერდები."""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.i18n import t
from app.models.ingredients import Ingredient
from app.models.system import User
from app.services import audit
from app.services import ingredients as svc
from app.services.errors import BusinessError, ValidationError
from app.services.numbers import parse_decimal
from app.services.text import looks_like_barcode
from app.services.units import all_units, from_base
from app.web.deps import get_current_user, get_db
from app.web.templating import flash, is_htmx, render

router = APIRouter(prefix="/ingredients")

STATUSES = ("active", "inactive", "all")


def _get_or_404(db: Session, ingredient_id: int) -> Ingredient:
    ing = db.get(Ingredient, ingredient_id)
    if ing is None:
        raise HTTPException(status_code=404)
    return ing


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------- სია და ძიება

@router.get("")
def list_ingredients(request: Request, q: str = "", status: str = "active", go: str = "",
                     db: Session = Depends(get_db)):
    if status not in STATUSES:
        status = "active"
    result = svc.search_ingredients(db, q, status)

    # Enter / სკანერი: თუ ცალსახად ერთი ინგრედიენტი მოიძებნა — პირდაპირ ვხსნით
    if go and q.strip():
        target = None
        if len(result.exact_barcode) == 1:
            target = result.exact_barcode[0]
        elif len(result.items) == 1:
            target = result.items[0]
        if target is not None:
            return _redirect(f"/ingredients/{target.id}")

    # თუ მიმდინარე ფილტრით ვერაფერი მოიძებნა — ვამოწმებთ სხვა სტატუსებშიც,
    # რომ მომხმარებელმა დუბლიკატი არ შექმნას (მაგ. დეაქტივირებული ინგრედიენტი).
    found_elsewhere = 0
    if not result.items and q.strip() and status != "all":
        found_elsewhere = svc.search_ingredients(db, q, "all", limit=1).total

    from app.models.documents import ItemType
    from app.services import stock

    ctx = {
        "active_nav": "ingredients",
        "balances": stock.balances(db, ItemType.INGREDIENT, ids=[i.id for i in result.items]),
        "found_elsewhere": found_elsewhere,
        "q": q,
        "status": status,
        "result": result,
        "q_is_barcode": looks_like_barcode(q),
    }
    if is_htmx(request):
        return render(request, "ingredients/_results.html", ctx)
    return render(request, "ingredients/list.html", ctx)


# ---------------------------------------------------------------- ფორმა

def _form_context(db: Session, *, ingredient: Ingredient | None, values: dict, errors: dict | None = None) -> dict:
    units = all_units(db)
    kind_locked = bool(ingredient and svc.is_ingredient_used(db, ingredient.id))
    return {
        "active_nav": "ingredients",
        "ingredient": ingredient,
        "units": units,
        "values": values,
        "errors": errors or {},
        "kind_locked": kind_locked,
        "locked_kind": ingredient.unit.kind if ingredient else None,
    }


def _default_unit_id(db: Session) -> int | None:
    units = all_units(db)
    kg = next((u for u in units if u.code == "kg"), None)
    return (kg or units[0]).id if units else None


async def _read_form(request: Request) -> tuple[dict, svc.IngredientInput, dict[str, list[str]]]:
    form = await request.form()
    values = {
        "name": str(form.get("name", "")),
        "unit_id": str(form.get("unit_id", "")),
        "min_stock": str(form.get("min_stock", "")),
        "barcodes": [str(b) for b in form.getlist("barcode")],
        "note": str(form.get("note", "")),
        "action": str(form.get("action", "")),
    }
    errors: dict[str, list[str]] = {}
    try:
        unit_id = int(values["unit_id"])
    except ValueError:
        unit_id = None
    try:
        min_stock = parse_decimal(values["min_stock"], places=3) or Decimal(0)
    except BusinessError as e:
        errors.setdefault("min_stock", []).append(e.msg.render())
        min_stock = Decimal(0)
    data = svc.IngredientInput(
        name=values["name"], unit_id=unit_id, min_stock=min_stock,
        barcodes=values["barcodes"], note=values["note"],
    )
    return values, data, errors


def _merge(errors: dict[str, list[str]], exc: ValidationError) -> dict[str, list[str]]:
    for field, msgs in exc.rendered().items():
        errors.setdefault(field, []).extend(msgs)
    return errors


def _collect_service_errors(db: Session, data: svc.IngredientInput, ing: Ingredient | None,
                            errors: dict[str, list[str]]) -> None:
    """ფორმაში უკვე არის შეცდომა (მაგ. რიცხვი) — დანარჩენ შემოწმებებსაც ვუშვებთ, რომ ყველა ერთად გამოჩნდეს."""
    try:
        svc.validate_ingredient(db, data, ing)
    except ValidationError as e:
        _merge(errors, e)


@router.get("/new")
def new_form(request: Request, name: str = "", barcode: str = "", db: Session = Depends(get_db)):
    values = {"name": name, "unit_id": str(_default_unit_id(db) or ""), "min_stock": "",
              "barcodes": [barcode] if barcode else [""], "note": ""}
    return render(request, "ingredients/form.html", _form_context(db, ingredient=None, values=values))


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    values, data, errors = await _read_form(request)
    if errors:
        _collect_service_errors(db, data, None, errors)
    else:
        try:
            ing = svc.create_ingredient(db, data, user)
            db.commit()
            flash(request, t("ingredients.created", name=ing.name))
            if values["action"] == "save_new":
                return _redirect("/ingredients/new")
            return _redirect(f"/ingredients/{ing.id}")
        except ValidationError as e:
            db.rollback()
            _merge(errors, e)
    if not values["barcodes"]:
        values["barcodes"] = [""]
    return render(request, "ingredients/form.html",
                  _form_context(db, ingredient=None, values=values, errors=errors), status_code=422)


@router.get("/{ingredient_id:int}")
def detail(request: Request, ingredient_id: int, db: Session = Depends(get_db)):
    ing = _get_or_404(db, ingredient_id)
    from app.models.documents import ItemType
    from app.services import costing, stock

    return render(request, "ingredients/detail.html", {
        "active_nav": "ingredients",
        "ing": ing,
        "balance": stock.balance(db, ItemType.INGREDIENT, ing.id),
        "unit_cost": costing.ingredient_costs(db, ids=[ing.id]).get(ing.id),
        "movements": stock.history(db, ItemType.INGREDIENT, ing.id),
        "used": svc.is_ingredient_used(db, ing.id),
        "history": audit.entries_for(db, svc.ENTITY, ing.id),
        "parse_changes": audit.parsed_changes,
    })


@router.get("/{ingredient_id:int}/edit")
def edit_form(request: Request, ingredient_id: int, db: Session = Depends(get_db)):
    ing = _get_or_404(db, ingredient_id)
    values = {
        "name": ing.name,
        "unit_id": str(ing.unit_id),
        "min_stock": _plain(from_base(ing.min_stock, ing.unit)),
        "barcodes": [b.barcode for b in ing.barcodes] or [""],
        "note": ing.note or "",
    }
    return render(request, "ingredients/form.html", _form_context(db, ingredient=ing, values=values))


def _plain(value: Decimal) -> str:
    from app.formatting import input_value
    return "" if value == 0 else input_value(value)


@router.post("/{ingredient_id:int}/edit")
async def update(request: Request, ingredient_id: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    ing = _get_or_404(db, ingredient_id)
    values, data, errors = await _read_form(request)
    if errors:
        _collect_service_errors(db, data, ing, errors)
    else:
        try:
            svc.update_ingredient(db, ing, data, user)
            db.commit()
            flash(request, t("ingredients.updated", name=ing.name))
            return _redirect(f"/ingredients/{ing.id}")
        except ValidationError as e:
            db.rollback()
            ing = _get_or_404(db, ingredient_id)
            _merge(errors, e)
    if not values["barcodes"]:
        values["barcodes"] = [""]
    return render(request, "ingredients/form.html",
                  _form_context(db, ingredient=ing, values=values, errors=errors), status_code=422)


# ---------------------------------------------------------------- სტატუსი / წაშლა

@router.post("/{ingredient_id:int}/deactivate")
def deactivate(request: Request, ingredient_id: int, db: Session = Depends(get_db),
               user: User | None = Depends(get_current_user)):
    ing = _get_or_404(db, ingredient_id)
    svc.set_active(db, ing, False, user)
    db.commit()
    flash(request, t("ingredients.deactivated", name=ing.name), "warning")
    return _redirect(f"/ingredients/{ing.id}")


@router.post("/{ingredient_id:int}/activate")
def activate(request: Request, ingredient_id: int, db: Session = Depends(get_db),
             user: User | None = Depends(get_current_user)):
    ing = _get_or_404(db, ingredient_id)
    svc.set_active(db, ing, True, user)
    db.commit()
    flash(request, t("ingredients.activated", name=ing.name))
    return _redirect(f"/ingredients/{ing.id}")


@router.post("/{ingredient_id:int}/delete")
def delete(request: Request, ingredient_id: int, db: Session = Depends(get_db),
           user: User | None = Depends(get_current_user)):
    ing = _get_or_404(db, ingredient_id)
    name = ing.name
    try:
        svc.delete_ingredient(db, ing, user)
        db.commit()
    except BusinessError as e:
        db.rollback()
        flash(request, e.msg.render(), "danger")
        return _redirect(f"/ingredients/{ingredient_id}")
    flash(request, t("ingredients.deleted", name=name))
    return _redirect("/ingredients")
