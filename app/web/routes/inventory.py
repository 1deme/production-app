"""ინვენტარიზაცია: ფაქტობრივი ნაშთის შეყვანა და სხვაობის კორექტირება."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatting import fmt_date, input_value
from app.i18n import t
from app.models.documents import DocStatus, InventoryCount, ItemType
from app.models.ingredients import Ingredient
from app.models.products import Product
from app.models.system import User
from app.services import audit, stock
from app.services import inventory as svc
from app.services.errors import BusinessError, ValidationError
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_date, get_decimal, get_int, get_or_404, redirect
from app.web.routes.receipts import doc_list_filters
from app.web.templating import flash, render

router = APIRouter(prefix="/inventory")


@router.get("")
def list_counts(request: Request, q: str = "", status: str = "", date_from: str = "", date_to: str = "",
                db: Session = Depends(get_db)):
    stmt = select(InventoryCount).where(*doc_list_filters(InventoryCount, q, status, date_from, date_to)) \
        .order_by(InventoryCount.date.desc(), InventoryCount.id.desc()).limit(300)
    return render(request, "inventory/list.html", {
        "active_nav": "inventory", "docs": list(db.scalars(stmt)), "q": q, "status": status,
        "date_from": date_from, "date_to": date_to,
    })


def _item_type(raw: str) -> ItemType:
    return ItemType.PRODUCT if raw == "product" else ItemType.INGREDIENT


def _ctx(db, item_type: ItemType, doc, values, actual: dict, errors=None):
    model = Ingredient if item_type == ItemType.INGREDIENT else Product
    items = list(db.scalars(select(model).where(model.is_active.is_(True)).order_by(model.search_name)))
    # დრაფტში შესაძლოა იყოს უკვე დეაქტივირებული საქონელიც
    known = {i.id for i in items}
    for item_id in actual:
        if item_id not in known and (obj := db.get(model, item_id)):
            items.append(obj)
    system = stock.balances(db, item_type, exclude_doc=("inventory", doc.id) if doc else None)
    return {"active_nav": "inventory", "doc": doc, "values": values, "item_type": item_type, "items": items,
            "system": system, "actual": actual, "errors": errors or FormErrors()}


async def _read(request, item_type: ItemType):
    form = await request.form()
    errors = FormErrors()
    values = {k: str(form.get(k, "")) for k in ("date", "note", "action")}
    actual_raw, actual = {}, {}
    for key, raw in form.multi_items():
        if key.startswith("actual_") and str(raw).strip():
            item_id = get_int(key[len("actual_"):])
            actual_raw[item_id] = str(raw)
            val = get_decimal(raw, errors, f"item:{item_id}", places=3)
            if item_id and val is not None:
                actual[item_id] = val
    data = svc.InventoryInput(date=get_date(values["date"], errors), item_type=item_type, note=values["note"],
                              actual=actual)
    return values, data, actual_raw, errors


async def _save(request, db, user, item_type, doc=None):
    values, data, actual_raw, errors = await _read(request, item_type)
    if not errors:
        try:
            c = svc.save_count(db, data, doc, confirm=values["action"] == "confirm", user=user)
            db.commit()
            flash(request, t("inventory.confirmed" if values["action"] == "confirm" else "inventory.saved", number=c.number))
            return redirect(f"/inventory/{c.id}")
        except ValidationError as e:
            db.rollback()
            errors.merge(e)
        except BusinessError as e:
            db.rollback()
            errors.add("_doc", e.msg.render())
    current = db.get(InventoryCount, doc.id) if doc else None
    return render(request, "inventory/form.html", _ctx(db, item_type, current, values, actual_raw, errors), 422)


@router.get("/new")
def new_form(request: Request, type: str = "ingredient", db: Session = Depends(get_db)):
    values = {"date": fmt_date(date.today()), "note": ""}
    return render(request, "inventory/form.html", _ctx(db, _item_type(type), None, values, {}))


@router.post("/new")
async def create(request: Request, type: str = "ingredient", db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    return await _save(request, db, user, _item_type(type))


@router.get("/{cid:int}")
def detail(request: Request, cid: int, db: Session = Depends(get_db)):
    c = get_or_404(db, InventoryCount, cid)
    return render(request, "inventory/detail.html", {
        "active_nav": "inventory", "doc": c, "audit_entries": audit.entries_for(db, svc.DOC, c.id),
        "parse_changes": audit.parsed_changes,
    })


@router.get("/{cid:int}/edit")
def edit_form(request: Request, cid: int, db: Session = Depends(get_db)):
    c = get_or_404(db, InventoryCount, cid)
    if c.status != DocStatus.DRAFT:
        flash(request, t("inventory.only_draft_edit"), "warning")
        return redirect(f"/inventory/{cid}")
    values = {"date": fmt_date(c.date), "note": c.note or ""}
    actual = {ln.item.id: input_value(ln.actual_qty_base / ln.item.unit.factor) for ln in c.lines}
    return render(request, "inventory/form.html", _ctx(db, c.item_type, c, values, actual))


@router.post("/{cid:int}/edit")
async def update(request: Request, cid: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    c = get_or_404(db, InventoryCount, cid)
    return await _save(request, db, user, c.item_type, c)


@router.post("/{cid:int}/{action}")
def action(request: Request, cid: int, action: str, db: Session = Depends(get_db),
           user: User | None = Depends(get_current_user)):
    c = get_or_404(db, InventoryCount, cid)
    number = c.number
    handlers = {"confirm": (svc.confirm_count, "inventory.confirmed"),
                "cancel": (svc.cancel_count, "inventory.cancelled"),
                "delete": (svc.delete_count, "inventory.deleted")}
    if action not in handlers:
        return redirect(f"/inventory/{cid}")
    fn, msg = handlers[action]
    try:
        fn(db, c, user)
        db.commit()
    except (BusinessError, ValidationError) as e:
        db.rollback()
        text = e.msg.render() if isinstance(e, BusinessError) else " ".join(sum(e.rendered().values(), []))
        flash(request, text, "danger")
        return redirect(f"/inventory/{cid}")
    flash(request, t(msg, number=number), "success" if action == "confirm" else "warning")
    return redirect("/inventory" if action == "delete" else f"/inventory/{cid}")
