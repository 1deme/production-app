"""ფორმებიდან მონაცემების წაკითხვა და შეცდომების მომზადება შაბლონისთვის."""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.i18n import t
from app.services.dates import parse_date
from app.services.errors import BusinessError, ValidationError
from app.services.numbers import parse_decimal


class FormErrors(dict):
    """{ველი: [ტექსტი, ...]}"""

    def add(self, field: str, message: str) -> None:
        self.setdefault(field, []).append(message)

    def merge(self, exc: ValidationError) -> "FormErrors":
        for field, msgs in exc.rendered().items():
            for m in msgs:
                self.add(field, m)
        return self

    def flat(self) -> list[str]:
        return [m for msgs in self.values() for m in msgs]


def get_decimal(raw, errors: FormErrors, field: str, *, places: int = 3, label: str | None = None,
                allow_negative: bool = False) -> Decimal | None:
    try:
        return parse_decimal(raw, places=places, allow_negative=allow_negative)
    except BusinessError as e:
        msg = e.msg.render()
        errors.add(field, f"{label}: {msg}" if label else msg)
        return None


def get_date(raw, errors: FormErrors, field: str = "date") -> date | None:
    try:
        return parse_date(raw)
    except BusinessError as e:
        errors.add(field, e.msg.render())
        return None


def get_int(raw) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def line_label(n: int) -> str:
    return t("docs.line_n", n=n)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404)
    return obj
