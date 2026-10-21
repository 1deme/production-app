"""Jinja2 შაბლონები, ქართული ფორმატირების ფუნქციები და შეტყობინებები (flash)."""
from dataclasses import dataclass

from fastapi import Request
from fastapi.templating import Jinja2Templates

from datetime import date

from app import config, formatting
from app.i18n import t
from app.services.i18n_labels import movement_label, status_label
from app.services.stock import doc_url


@dataclass
class NavItem:
    key: str
    url: str
    icon: str
    ready: bool = True
    group: str = ""


# მენიუ. ready=False — განყოფილება მომდევნო ეტაპზე ჩაირთვება.
NAV = [
    NavItem("dashboard", "/", "bi-house-door"),
    NavItem("ingredients", "/ingredients", "bi-basket", group="nav.group_stock"),
    NavItem("receipts", "/receipts", "bi-truck"),
    NavItem("recipes", "/recipes", "bi-journal-text"),
    NavItem("production", "/production", "bi-gear-wide-connected"),
    NavItem("products", "/products", "bi-box-seam"),
    NavItem("dispatch", "/dispatch", "bi-send"),
    NavItem("inventory", "/inventory", "bi-clipboard-check"),
    NavItem("reports", "/reports", "bi-bar-chart", group="nav.group_other"),
    NavItem("audit", "/audit", "bi-clock-history"),
    NavItem("settings", "/settings", "bi-sliders"),
]

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.globals.update(
    t=t,
    nav=NAV,
    fmt_money=formatting.fmt_money,
    fmt_num=formatting.fmt_num,
    fmt_price=formatting.fmt_price,
    fmt_qty=formatting.fmt_qty,
    fmt_qty_value=formatting.fmt_qty_value,
    fmt_date=formatting.fmt_date,
    fmt_datetime=formatting.fmt_datetime,
    fmt_size=formatting.fmt_size,
    input_value=formatting.input_value,
    status_label=status_label,
    movement_label=movement_label,
    doc_url=doc_url,
    today=date.today,
)
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True


# ---------------------------------------------------------------- flash შეტყობინებები

def flash(request: Request, message: str, level: str = "success") -> None:
    """შეტყობინება, რომელიც გამოჩნდება შემდეგ გვერდზე (მაგ. „შენახულია“)."""
    # სიას თავიდან ვანიჭებთ (და არა append-ით ვცვლით), რომ სესიამ ცვლილება აუცილებლად შეინახოს
    messages = list(request.session.get("_flash", []))
    messages.append({"message": message, "level": level})
    request.session["_flash"] = messages


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flash", []) if "session" in request.scope else []


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"


def _demo_mode(request: Request) -> bool:
    """დემო-რეჟიმის ბანერისთვის (ყველა გვერდზე)."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return False
    try:
        from app.services.demo import is_demo

        with factory() as s:
            return is_demo(s)
    except Exception:  # noqa: BLE001 — ბანერის გამო გვერდი არ უნდა ჩავარდეს
        return False


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    if not is_htmx(request):
        ctx.setdefault("demo_mode", _demo_mode(request))
    ctx.setdefault("active_nav", "")
    # HTMX-ის ნაწილობრივი პასუხი შეტყობინებებს არ აჩვენებს — ამიტომ არც „ვხარჯავთ“ მათ
    ctx["flashes"] = [] if is_htmx(request) else pop_flashes(request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
