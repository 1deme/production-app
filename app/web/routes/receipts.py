"""მიღების ზედნადებები."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.formatting import fmt_date, input_value
from app.i18n import t
from app.models.documents import DocStatus, Receipt
from app.models.ingredients import Ingredient
from app.models.products import Counterparty
from app.models.system import User
from app.services import audit
from app.services import receipts as svc
from app.services.dates import parse_date
from app.services.documents import counterparty_names
from app.services.errors import BusinessError, ValidationError
from app.services.text import escape_like, normalize_text
from app.services.units import all_units
from app.web.deps import get_current_user, get_db
from app.web.forms import FormErrors, get_date, get_decimal, get_int, get_or_404, line_label, redirect
from app.web.routes.recipes import units_by_kind
from app.web.templating import flash, render

router = APIRouter(prefix="/receipts")


def doc_list_filters(model, q: str, status: str, date_from: str, date_to: str):
    """საერთო ფილტრები საბუთების სიებისთვის."""
    conds = []
    if status in ("draft", "confirmed", "cancelled"):
        conds.append(model.status == DocStatus(status))
    try:
        d1 = parse_date(date_from, required=False)
        d2 = parse_date(date_to, required=False)
    except BusinessError:
        d1 = d2 = None
    if d1:
        conds.append(model.date >= d1)
    if d2:
        conds.append(model.date <= d2)
    qn = normalize_text(q)
    if qn:
        like = f"%{escape_like(qn)}%"
        alts = [func.lower(model.number).like(like, escape="\\")]
        if hasattr(model, "counterparty_id"):
            alts.append(model.counterparty.has(Counterparty.search_name.like(like, escape="\\")))
        conds.append(or_(*alts))
    return conds


@router.get("")
def list_receipts(request: Request, q: str = "", status: str = "", date_from: str = "", date_to: str = "",
                  db: Session = Depends(get_db)):
    stmt = select(Receipt).where(*doc_list_filters(Receipt, q, status, date_from, date_to)) \
        .order_by(Receipt.date.desc(), Receipt.id.desc()).limit(300)
    return render(request, "receipts/list.html", {
        "active_nav": "receipts", "docs": list(db.scalars(stmt)), "q": q, "status": status,
        "date_from": date_from, "date_to": date_to,
    })


@router.get("/line-row")
def line_row(request: Request, id: int, db: Session = Depends(get_db)):
    ing = get_or_404(db, Ingredient, id)
    return render(request, "receipts/_line.html", {
        "ln": {"ingredient": ing, "qty": "", "unit_id": ing.unit_id, "unit_price": "", "amount": "", "source": "price"},
        "ubk": units_by_kind(db), "errors": FormErrors(), "idx": 0,
    })


def _ctx(db, receipt, values, lines, errors=None):
    return {"active_nav": "receipts", "receipt": receipt, "values": values, "lines": lines,
            "errors": errors or FormErrors(), "ubk": units_by_kind(db), "units": all_units(db),
            "suppliers": counterparty_names(db, supplier=True)}


async def _read(request, db):
    form = await request.form()
    errors = FormErrors()
    values = {k: str(form.get(k, "")) for k in ("date", "supplier", "note", "action")}
    cols = [form.getlist(k) for k in ("line_ingredient_id", "line_qty", "line_unit_id", "line_price",
                                      "line_amount", "line_source")]
    lines_in, lines_view = [], []
    for i, (iid, q, uid, price, amount, source) in enumerate(zip(*cols), start=1):
        lbl = line_label(i)
        qty = get_decimal(q, errors, f"line:{i}", places=3, label=lbl)
        p = get_decimal(price, errors, f"line:{i}", places=4, label=lbl)
        a = get_decimal(amount, errors, f"line:{i}", places=2, label=lbl)
        lines_in.append(svc.ReceiptLineInput(get_int(iid), qty, get_int(uid), p, a, source or "price"))
        ing = db.get(Ingredient, get_int(iid))
        if ing:
            lines_view.append({"ingredient": ing, "qty": q, "unit_id": get_int(uid), "unit_price": price,
                               "amount": amount, "source": source})
    data = svc.ReceiptInput(date=get_date(values["date"], errors), supplier=values["supplier"],
                            note=values["note"], lines=lines_in)
    return values, data, lines_view, errors


def _save(request, db, user, receipt=None):
    async def run():
        values, data, lines, errors = await _read(request, db)
        if not errors:
            try:
                r = svc.save_receipt(db, data, receipt, confirm=values["action"] == "confirm", user=user)
                db.commit()
                key = "receipts.confirmed" if values["action"] == "confirm" else "receipts.saved"
                flash(request, t(key, number=r.number))
                return redirect(f"/receipts/{r.id}")
            except ValidationError as e:
                db.rollback()
                errors.merge(e)
            except BusinessError as e:
                db.rollback()
                errors.add("_doc", e.msg.render())
        current = db.get(Receipt, receipt.id) if receipt else None
        return render(request, "receipts/form.html", _ctx(db, current, values, lines, errors), 422)
    return run()


@router.get("/new")
def new_form(request: Request, db: Session = Depends(get_db)):
    values = {"date": fmt_date(date.today()), "supplier": "", "note": ""}
    return render(request, "receipts/form.html", _ctx(db, None, values, []))


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    return await _save(request, db, user)


@router.get("/{rid:int}")
def detail(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, Receipt, rid)
    return render(request, "receipts/detail.html", {
        "active_nav": "receipts", "doc": r, "audit_entries": audit.entries_for(db, svc.DOC, r.id),
        "parse_changes": audit.parsed_changes,
    })


@router.get("/{rid:int}/edit")
def edit_form(request: Request, rid: int, db: Session = Depends(get_db)):
    r = get_or_404(db, Receipt, rid)
    if r.status == DocStatus.CANCELLED:
        flash(request, t("docs.cancelled_readonly"), "warning")
        return redirect(f"/receipts/{rid}")
    values = {"date": fmt_date(r.date), "supplier": r.counterparty.name if r.counterparty else "", "note": r.note or ""}
    lines = [{"ingredient": ln.ingredient, "qty": input_value(ln.qty), "unit_id": ln.unit_id,
              "unit_price": input_value(ln.unit_price, 4), "amount": input_value(ln.amount, 2), "source": "price"}
             for ln in r.lines]
    return render(request, "receipts/form.html", _ctx(db, r, values, lines))


@router.post("/{rid:int}/edit")
async def update(request: Request, rid: int, db: Session = Depends(get_db),
                 user: User | None = Depends(get_current_user)):
    return await _save(request, db, user, get_or_404(db, Receipt, rid))


@router.post("/{rid:int}/{action}")
def action(request: Request, rid: int, action: str, db: Session = Depends(get_db),
           user: User | None = Depends(get_current_user)):
    r = get_or_404(db, Receipt, rid)
    number = r.number
    handlers = {"confirm": (svc.confirm_receipt, "receipts.confirmed"),
                "cancel": (svc.cancel_receipt, "receipts.cancelled"),
                "delete": (svc.delete_receipt, "receipts.deleted")}
    if action not in handlers:
        return redirect(f"/receipts/{rid}")
    fn, msg = handlers[action]
    try:
        fn(db, r, user)
        db.commit()
    except (BusinessError, ValidationError) as e:
        db.rollback()
        text = e.msg.render() if isinstance(e, BusinessError) else " ".join(sum(e.rendered().values(), []))
        flash(request, text, "danger")
        return redirect(f"/receipts/{rid}")
    flash(request, t(msg, number=number), "warning" if action != "confirm" else "success")
    return redirect("/receipts" if action == "delete" else f"/receipts/{rid}")


@router.get("/{rid:int}/pdf")
def pdf(rid: int, db: Session = Depends(get_db)):
    from app.services.reports.pdf import receipt_pdf
    from app.web.routes.reports import file_response

    r = get_or_404(db, Receipt, rid)
    return file_response(receipt_pdf(r), f"{r.number}.pdf", "application/pdf", inline=True)
