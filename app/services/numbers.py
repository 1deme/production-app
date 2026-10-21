"""მომხმარებლის მიერ შეყვანილი რიცხვების წაკითხვა.

მიიღება როგორც ქართული (12,5 · 1 234,50), ისე ინგლისური (12.5 · 1,234.50) ჩანაწერი.
თუ მხოლოდ მძიმეა, ის ყოველთვის ათწილადის გამყოფად ითვლება (ქართული სტანდარტი).
"""
import re
from decimal import Decimal, InvalidOperation

from app.services.errors import BusinessError

_IGNORED = re.compile(r"[\s  '₾]")
_PLAIN = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")
MAX_ABS = Decimal(10) ** 12


def parse_decimal(
    raw,
    *,
    places: int = 3,
    allow_negative: bool = False,
    required: bool = False,
) -> Decimal | None:
    """აბრუნებს Decimal-ს ან None-ს (ცარიელი ველი). შეცდომისას — BusinessError."""
    if raw is None:
        raw = ""
    if isinstance(raw, Decimal):
        s = format(raw, "f")
    else:
        s = str(raw)
    s = _IGNORED.sub("", s.strip())
    if not s:
        if required:
            raise BusinessError("common.required")
        return None

    negative = False
    if s[0] in "+-−":
        negative = s[0] != "+"
        s = s[1:]

    if "," in s and "." in s:
        # ბოლო გამყოფი — ათწილადი, დანარჩენი — ათასეულები
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        int_part, _, frac = s.rpartition(dec)
        if dec in int_part or not _valid_grouping(int_part, thou):
            raise BusinessError("errors.invalid_number")
        s = int_part.replace(thou, "") + "." + frac
    elif "," in s:
        if s.count(",") > 1:
            raise BusinessError("errors.invalid_number")
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        raise BusinessError("errors.invalid_number")

    if not _PLAIN.match(s):
        raise BusinessError("errors.invalid_number")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise BusinessError("errors.invalid_number") from None

    if negative:
        value = -value
    if value < 0 and not allow_negative:
        raise BusinessError("errors.negative_not_allowed")
    if abs(value) >= MAX_ABS:
        raise BusinessError("errors.too_large")
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > places:
        raise BusinessError("errors.too_many_decimals", places=places)
    return value


def _valid_grouping(int_part: str, sep: str) -> bool:
    groups = int_part.split(sep)
    if not groups[0].isdigit() or len(groups[0]) > 3:
        return False
    return all(len(g) == 3 and g.isdigit() for g in groups[1:])
