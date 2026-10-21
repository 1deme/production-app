"""PDF: რეპორტები და საბუთების ბეჭდვითი ფორმა. ქართული შრიფტი (Noto Sans Georgian) ჩაშენებულია."""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import config
from app.formatting import fmt_date, fmt_datetime, fmt_money, fmt_num, fmt_price, fmt_qty, format_number
from app.i18n import t
from app.services.reports.data import Report

# Noto Sans Georgian შეიცავს მხოლოდ ქართულ ასოებს (ციფრები, ლათინური და პუნქტუაცია მასში არ არის).
# ბრაუზერი თავად იყენებს სათადარიგო შრიფტს, ReportLab კი — არა, ამიტომ ტექსტს ვყოფთ ნაწილებად:
# ქართული → Noto Sans Georgian, დანარჩენი → Noto Sans.
FONT, FONT_BOLD = "NotoGeorgian", "NotoGeorgian-Bold"
LATIN, LATIN_BOLD = "NotoSans", "NotoSans-Bold"
_registered = False
_georgian_chars: set[int] = set()


def _fonts():
    global _registered
    if not _registered:
        geo = TTFont(FONT, str(config.FONTS_DIR / "NotoSansGeorgian-Regular.ttf"))
        pdfmetrics.registerFont(geo)
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(config.FONTS_DIR / "NotoSansGeorgian-Bold.ttf")))
        pdfmetrics.registerFont(TTFont(LATIN, str(config.FONTS_DIR / "NotoSans-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(LATIN_BOLD, str(config.FONTS_DIR / "NotoSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD)
        pdfmetrics.registerFontFamily(LATIN, normal=LATIN, bold=LATIN_BOLD)
        _georgian_chars.update(c for c in geo.face.charToGlyph if c > 0x7F and c != 0x20BE)
        _registered = True


def _runs(text: str):
    """[(ქართულია?, ტექსტი), ...] — ჰარი მიმდინარე ნაწილს მიჰყვება."""
    out = []
    for ch in text:
        geo = ord(ch) in _georgian_chars if not ch.isspace() else (out[-1][0] if out else True)
        if out and out[-1][0] == geo:
            out[-1][1] += ch
        else:
            out.append([geo, ch])
    return out


def _mix(text: str, bold: bool = False) -> str:
    """Paragraph-ის მარკაპი: არაქართული ნაწილები Noto Sans-ით. bold — სტილი, რომელიც უკვე სქელია."""
    _fonts()
    latin = LATIN_BOLD if bold else LATIN
    return "".join(_esc(part) if geo else f'<font name="{latin}">{_esc(part)}</font>' for geo, part in _runs(str(text)))


def _draw(canvas, x: float, y: float, text: str, size: float, right: bool = False):
    _fonts()
    runs = [(FONT if geo else LATIN, part) for geo, part in _runs(text)]
    width = sum(pdfmetrics.stringWidth(part, font, size) for font, part in runs)
    if right:
        x -= width
    for font, part in runs:
        canvas.setFont(font, size)
        canvas.drawString(x, y, part)
        x += pdfmetrics.stringWidth(part, font, size)


def _styles():
    _fonts()
    return {
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=15, leading=19, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName=FONT, fontSize=9, leading=12, textColor=colors.HexColor("#555555")),
        "h2": ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8, leading=10),
        "cell_r": ParagraphStyle("cell_r", fontName=FONT, fontSize=8, leading=10, alignment=TA_RIGHT),
        "head": ParagraphStyle("head", fontName=FONT_BOLD, fontSize=8, leading=10),
        "head_r": ParagraphStyle("head_r", fontName=FONT_BOLD, fontSize=8, leading=10, alignment=TA_RIGHT),
        "normal": ParagraphStyle("normal", fontName=FONT, fontSize=10, leading=13),
    }


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_cell(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "qty":
        return format_number(value, 0, 4)
    if kind == "price":
        return fmt_price(value)
    if kind == "money":
        return fmt_money(value)
    if kind == "num":
        return fmt_num(value)
    if kind == "percent":
        return format_number(value, 1, 1) + "%"
    if kind == "date":
        return fmt_date(value)
    return str(value)


def _table(header: list, rows: list, right: list[bool], widths=None, bold_last=False, red_rows=()):
    st = _styles()
    data = [[Paragraph(_mix(h, True), st["head_r" if r else "head"]) for h, r in zip(header, right)]]
    for row in rows:
        data.append([Paragraph(_mix(v), st["cell_r" if r else "cell"]) for v, r in zip(row, right)])
    tbl = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9AA8BC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DCE6")),
    ]
    if bold_last and rows:
        style += [("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF1F5")),
                  ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#9AA8BC"))]
    for i in red_rows:
        style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), colors.HexColor("#FDECEC")))
    tbl.setStyle(TableStyle(style))
    return tbl


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#777777"))
    _draw(canvas, doc.leftMargin, 8 * mm, f"{t('app.name')} · {fmt_datetime(datetime.now())}", 7)
    _draw(canvas, doc.pagesize[0] - doc.rightMargin, 8 * mm, t("reports.page", n=doc.page), 7, right=True)
    canvas.restoreState()


def _build(story, wide: bool) -> bytes:
    buf = io.BytesIO()
    size = landscape(A4) if wide else A4
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=15 * mm)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def report_pdf(report: Report) -> bytes:
    st = _styles()
    story = [Paragraph(_mix(report.title, True), st["title"]), Paragraph(_mix(report.period), st["sub"]), Spacer(1, 4 * mm)]
    wide = any(len(s.columns) > 5 for s in report.sections)
    for sec in report.sections:
        if len(report.sections) > 1:
            story.append(Paragraph(_mix(sec.title, True), st["h2"]))
        right = [c.kind not in ("text",) for c in sec.columns]
        rows = [[fmt_cell(r.get(c.key), c.kind) for c in sec.columns] for r in sec.rows]
        if sec.totals:
            rows.append([fmt_cell(sec.totals.get(c.key), c.kind) if sec.totals.get(c.key) is not None else ""
                         for c in sec.columns])
        if not sec.rows:
            story.append(Paragraph(_mix(t("reports.no_data")), st["sub"]))
            continue
        red = [i for i, f in enumerate(sec.row_flags) if f == "low"]
        story.append(_table([c.label for c in sec.columns], rows, right, bold_last=bool(sec.totals), red_rows=red))
        if sec.note:
            story.append(Paragraph(_mix(sec.note), st["sub"]))
    return _build(story, wide)


# ---------------------------------------------------------------- საბუთები

def _doc_header(title: str, meta: list[tuple[str, str]]):
    st = _styles()
    story = [Paragraph(_mix(title, True), st["title"]), Spacer(1, 3 * mm)]
    data = [[Paragraph(f"<b>{_mix(k + ':', True)}</b> {_mix(v)}", st["normal"])] for k, v in meta if v]
    if data:
        story.append(Table(data, colWidths=[186 * mm], style=[("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(Spacer(1, 4 * mm))
    return story


def _signatures(left: str, right: str):
    st = _styles()
    line = "_" * 28
    return [Spacer(1, 14 * mm), Table(
        [[Paragraph(_mix(f"{left}: {line}"), st["normal"]), Paragraph(_mix(f"{right}: {line}"), st["normal"])]],
        colWidths=[93 * mm, 93 * mm])]


def receipt_pdf(r) -> bytes:
    story = _doc_header(t("receipts.doc_title", number=r.number), [
        (t("common.date"), fmt_date(r.date)),
        (t("receipts.supplier"), r.counterparty.name if r.counterparty else ""),
        (t("common.status"), t(f"status.{r.status.value}")),
        (t("common.note"), r.note or ""),
    ])
    rows = [[str(i), ln.ingredient.name, f"{fmt_num(ln.qty, 3)} {ln.unit.name}",
             fmt_price(ln.unit_price), fmt_money(ln.amount)] for i, ln in enumerate(r.lines, start=1)]
    rows.append(["", t("docs.total"), "", "", fmt_money(r.total)])
    story.append(_table(["#", t("nav.ingredients"), t("docs.qty"), t("receipts.unit_price"), t("receipts.amount")],
                        rows, [False, False, True, True, True], [10 * mm, 86 * mm, 30 * mm, 30 * mm, 30 * mm],
                        bold_last=True))
    story += _signatures(t("docs.handed_over"), t("docs.received_by"))
    return _build(story, False)


def dispatch_pdf(d) -> bytes:
    story = _doc_header(t("dispatch.doc_title", number=d.number), [
        (t("common.date"), fmt_date(d.date)),
        (t("dispatch.customer"), d.counterparty.name if d.counterparty else ""),
        (t("common.status"), t(f"status.{d.status.value}")),
        (t("common.note"), d.note or ""),
    ])
    rows = [[str(i), ln.product.name, fmt_qty(ln.qty_base, ln.product.unit),
             fmt_price(ln.unit_price) if ln.unit_price is not None else "—",
             fmt_money(ln.amount) if ln.amount is not None else "—"] for i, ln in enumerate(d.lines, start=1)]
    rows.append(["", t("docs.total"), "", "", fmt_money(d.total)])
    story.append(_table(["#", t("nav.products"), t("docs.qty"), t("dispatch.price"), t("receipts.amount")],
                        rows, [False, False, True, True, True], [10 * mm, 86 * mm, 30 * mm, 30 * mm, 30 * mm],
                        bold_last=True))
    story += _signatures(t("docs.handed_over"), t("docs.received_by"))
    return _build(story, False)
