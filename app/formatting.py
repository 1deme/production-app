"""რიცხვების, თანხების და თარიღების ქართული ფორმატით გამოტანა.

- თანხა:      1 234,50 ₾  (ყოველთვის 2 ათწილადი)
- რაოდენობა:  1 234,5 კგ  (ბოლო ნულები იშლება; მაქს. 3 ათწილადი, რომ მაგ. 0,125 კგ = 125 გ არ დაიკარგოს)
- თარიღი:     21.10.2026
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.db_types import to_decimal

NBSP = " "  # ჰარი, რომელიც სტრიქონს არ წყვეტს (რიცხვი და ერთეული ერთად დარჩეს)
QTY_DISPLAY_PLACES = 3


def format_number(value, min_places: int = 0, max_places: int = 2, group: bool = True) -> str:
    if value is None:
        return ""
    d = to_decimal(value).quantize(Decimal(1).scaleb(-max_places), rounding=ROUND_HALF_UP)
    negative = d < 0
    s = format(abs(d), "f")
    int_part, _, frac = s.partition(".")
    frac = frac.rstrip("0")
    if len(frac) < min_places:
        frac = frac.ljust(min_places, "0")
    if group:
        int_part = _group_thousands(int_part)
    out = int_part + ("," + frac if frac else "")
    return ("-" if negative and d != 0 else "") + out


def _group_thousands(digits: str) -> str:
    parts = []
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    parts.insert(0, digits)
    return NBSP.join(parts)


def fmt_money(value, with_symbol: bool = True) -> str:
    if value is None:
        return ""
    s = format_number(value, 2, 2)
    return f"{s}{NBSP}₾" if with_symbol else s


def fmt_price(value, with_symbol: bool = True) -> str:
    """ერთეულის ფასი: მინ. 2, მაქს. 4 ათწილადი (მაგ. საფუარი 0,045 ₾/გ)."""
    if value is None:
        return ""
    s = format_number(value, 2, 4)
    return f"{s}{NBSP}₾" if with_symbol else s


def fmt_num(value, max_places: int = 2) -> str:
    return format_number(value, 0, max_places)


def qty_places(unit) -> int:
    """საბაზო ერთეულში (გ, მლ, ცალი) — 3 ათწილადი; კგ/ლ-ში — 4 (0,0306 კგ = 30,6 გ არ დაიკარგოს)."""
    return QTY_DISPLAY_PLACES if unit.factor == 1 else QTY_DISPLAY_PLACES + 1


def fmt_qty_value(qty_base, unit) -> str:
    """საბაზო ერთეულის რაოდენობა → ჩვენების ერთეულში, ერთეულის გარეშე."""
    if qty_base is None:
        return ""
    return format_number(to_decimal(qty_base) / unit.factor, 0, qty_places(unit))


def fmt_qty(qty_base, unit) -> str:
    """საბაზო ერთეულის რაოდენობა → „1,5 კგ“."""
    if qty_base is None:
        return ""
    return f"{fmt_qty_value(qty_base, unit)}{NBSP}{unit.name}"


def input_value(value, max_places: int = QTY_DISPLAY_PLACES) -> str:
    """ფორმის ველში ჩასაწერი მნიშვნელობა: „1234,5“ (დაჯგუფების გარეშე, რომ რედაქტირება ადვილი იყოს)."""
    if value is None:
        return ""
    return format_number(value, 0, max_places, group=False)


def fmt_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def fmt_datetime(value) -> str:
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def fmt_size(num_bytes: int) -> str:
    from app.i18n import t

    if num_bytes < 1024 * 1024:
        return t("format.size_kb", n=format_number(Decimal(num_bytes) / 1024, 0, 1))
    return t("format.size_mb", n=format_number(Decimal(num_bytes) / (1024 * 1024), 0, 1))
