"""მზა პროდუქციის გატანა."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatting import fmt_date, input_value
from app.i18n import t
from app.models.documents import Dispatch, DocStatus, ItemType
from app.models.products import Product
from app.models.system import User
from app.services import audit, stock
from app.services import dispatch as svc
from app.services.documents import counterparty_names
from app.services.errors import BusinessError, ValidationError
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_date, get_decimal, get_int, get_or_404, line_label, redirect
from app.web.routes.receipts import doc_list_filters
from app.web.templating import flash, render

router = APIRouter(prefix="/dispatch")


@router.get("")
def list_dispatches(request: Request, q: str = "", status: str = "", date_from: str = "", date_to: str = "",
                    db: Session = Depends(get_db)):
    stmt = select(Dispatch).where(*doc_list_filters(Dispatch, q, status, date_from, date_to)) \
        .order_by(Dispatch.date.desc(), Dispatch.id.desc()).limit(300)
    return render(request, "dispatch/list.html", {
        "active_nav": "dispatch", "docs": list(db.scalars(stmt)), "q": q, "status": status,
        "date_from": date_from, "date_to": date_to,
    })


def _available(db, product_ids, exclude_id=None):
    """ხელმისაწვდომი ნაშთი (ამ საბუთის საკუთარი გატანის გარეშე, რედაქტირებისას)."""
    return stock.balances(db, ItemType.PRODUCT, ids=product_ids, exclude_doc=("dispatch", exclude_id) if exclude_id else None)


@router.get("/line-row")
def line_row(request: Request, id: int, doc_id: int | None = None, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, id)
    avail = _available(db, [p.id], doc_id)
    return render(request, "dispatch/_line.html", {
        "ln": {"product": p, "qty": "", "unit_price": ""}, "avail": avail, "errors": FormErrors(), "idx": 0,
    })


def _ctx(db, doc, values, lines, errors=None):
    avail = _available(db, [ln["product"].id for ln in lines], doc.id if doc else None)
    return {"active_nav": "dispatch", "doc": doc, "values": values, "lines": lines, "avail": avail,
            "errors": errors or FormErrors(), "customers": counterparty_names(db, customer=True)}


async def _read(request, db):
    form = await request.form()
    errors = FormErrors()
    values = {k: str(form.get(k, "")) for k in ("date", "customer", "note", "action")}
    lines_in, lines_view = [], []
    cols = [form.getlist(k) for k in ("line_product_id", "line_qty", "line_price")]
    for i, (pid, q, price) in enumerate(zip(*cols), start=1):
        qty = get_decimal(q, errors, f"line:{i}", places=3, label=line_label(i))
        p = get_decimal(price, errors, f"line:{i}", places=4, label=line_label(i))
        lines_in.append(svc.DispatchLineInput(get_int(pid), qty, p))
        prod = db.get(Product, get_int(pid))
        if prod:
            lines_view.append({"product": prod, "qty": q, "unit_price": price})
    data = svc.DispatchInput(date=get_date(values["date"], errors), customer=values["customer"],
                             note=values["note"], lines=lines_in)
    return values, data, lines_view, errors


async def _save(request, db, user, doc=None):
    values, data, lines, errors = await _read(request, db)
    if not errors:
        try:
            d = svc.save_dispatch(db, data, doc, confirm=values["action"] == "confirm", user=user)
            db.commit()
            flash(request, t("dispatch.confirmed" if values["action"] == "confirm" else "dispatch.saved", number=d.number))
            return redirect(f"/dispatch/{d.id}")
        except ValidationError as e:
            db.rollback()
            errors.merge(e)
        except BusinessError as e:
            db.rollback()
            errors.add("_doc", e.msg.render())
    current = db.get(Dispatch, doc.id) if doc else None
    return render(request, "dispatch/form.html", _ctx(db, current, values, lines, errors), 422)


@router.get("/new")
def new_form(request: Request, db: Session = Depends(get_db)):
    values = {"date": fmt_date(date.today()), "customer": "", "note": ""}
    return render(request, "dispatch/form.html", _ctx(db, None, values, []))


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    return await _save(request, db, user)


@router.get("/{did:int}")
def detail(request: Request, did: int, db: Session = Depends(get_db)):
    d = get_or_404(db, Dispatch, did)
    return render(request, "dispatch/detail.html", {
        "active_nav": "dispatch", "doc": d, "audit_entries": audit.entries_for(db, svc.DOC, d.id),
        "parse_changes": audit.parsed_changes,
    })


@router.get("/{did:int}/edit")
def edit_form(request: Request, did: int, db: Session = Depends(get_db)):
    d = get_or_404(db, Dispatch, did)
    if d.status == DocStatus.CANCELLED:
        flash(request, t("docs.cancelled_readonly"), "warning")
        return redirect(f"/dispatch/{did}")
    values = {"date": fmt_date(d.date), "customer": d.counterparty.name if d.counterparty else "", "note": d.note or ""}
    lines = [{"product": ln.product, "qty": input_value(ln.qty_base / ln.product.unit.factor),
              "unit_price": input_value(ln.unit_price, 4) if ln.unit_price is not None else ""} for ln in d.lines]
    return render(request, "dispatch/form.html", _ctx(db, d, values, lines))


@router.post("/{did:int}/edit")
async def update(request: Request, did: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    return await _save(request, db, user, get_or_404(db, Dispatch, did))


@router.post("/{did:int}/{action}")
def action(request: Request, did: int, action: str, db: Session = Depends(get_db),
           user: User | None = Depends(get_current_user)):
    d = get_or_404(db, Dispatch, did)
    number = d.number
    handlers = {"confirm": (svc.confirm_dispatch, "dispatch.confirmed"),
                "cancel": (svc.cancel_dispatch, "dispatch.cancelled"),
                "delete": (svc.delete_dispatch, "dispatch.deleted")}
    if action not in handlers:
        return redirect(f"/dispatch/{did}")
    fn, msg = handlers[action]
    try:
        fn(db, d, user)
        db.commit()
    except (BusinessError, ValidationError) as e:
        db.rollback()
        text = e.msg.render() if isinstance(e, BusinessError) else " ".join(sum(e.rendered().values(), []))
        flash(request, text, "danger")
        return redirect(f"/dispatch/{did}")
    flash(request, t(msg, number=number), "success" if action == "confirm" else "warning")
    return redirect("/dispatch" if action == "delete" else f"/dispatch/{did}")


@router.get("/{did:int}/pdf")
def pdf(did: int, db: Session = Depends(get_db)):
    from app.services.reports.pdf import dispatch_pdf
    from app.web.routes.reports import file_response

    d = get_or_404(db, Dispatch, did)
    return file_response(dispatch_pdf(d), f"{d.number}.pdf", "application/pdf", inline=True)
