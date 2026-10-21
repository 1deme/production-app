"""რეპორტები: HTML, Excel, PDF."""
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.formatting import fmt_date
from app.i18n import t
from app.services.dates import month_start, parse_date
from app.services.errors import BusinessError
from app.services.reports import data as reports
from app.services.reports.excel import to_xlsx
from app.services.reports.pdf import fmt_cell, report_pdf
from app.web.deps import get_db
from app.web.templating import render

router = APIRouter(prefix="/reports")


def file_response(content: bytes, filename: str, media_type: str, inline: bool = False) -> Response:
    # ქართული სახელი: RFC 5987 (filename*), ძველი ბრაუზერებისთვის — ლათინური სათადარიგო
    ascii_name = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in filename)
    disp = "inline" if inline else "attachment"
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"})


@router.get("")
def index(request: Request):
    return render(request, "reports/index.html", {"active_nav": "reports", "reports": reports.REPORTS})


@router.get("/{key}")
def view(request: Request, key: str, date_from: str = "", date_to: str = "", format: str = "html",
         db: Session = Depends(get_db)):
    if key not in reports.REPORTS:
        raise HTTPException(status_code=404)
    error = None
    today = date.today()
    try:
        start = parse_date(date_from, required=False) or month_start(today)
        end = parse_date(date_to, required=False) or today
    except BusinessError as e:
        error = e.msg.render()
        start, end = month_start(today), today
    if start > end:
        error = t("reports.bad_period")
        start, end = end, start
    report = reports.build(db, key, start, end)
    fname = f"{report.title} {fmt_date(start)}-{fmt_date(end)}"
    if format == "xlsx":
        return file_response(to_xlsx(report), f"{fname}.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if format == "pdf":
        return file_response(report_pdf(report), f"{fname}.pdf", "application/pdf", inline=True)
    return render(request, "reports/view.html", {
        "active_nav": "reports", "report": report, "key": key, "error": error,
        "date_from": fmt_date(start), "date_to": fmt_date(end), "fmt_cell": fmt_cell,
    })
