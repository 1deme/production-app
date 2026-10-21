"""თარიღების წაკითხვა. ძირითადი ფორმატი: დდ.თთ.წწწწ (21.10.2026)."""
import re
from datetime import date

from app.services.errors import BusinessError

_DMY = re.compile(r"^(\d{1,2})[./\-\s](\d{1,2})[./\-\s](\d{2}|\d{4})$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_COMPACT = re.compile(r"^(\d{2})(\d{2})(\d{4})$")  # 21102026


def parse_date(raw, *, required: bool = True) -> date | None:
    s = (raw or "").strip() if not isinstance(raw, date) else raw
    if isinstance(s, date):
        return s
    if not s:
        if required:
            raise BusinessError("common.required")
        return None
    m = _DMY.match(s) or _COMPACT.match(s)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(y) + 2000 if len(y) == 2 else int(y)
    else:
        m = _YMD.match(s)
        if not m:
            raise BusinessError("errors.invalid_date")
        year, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        result = date(year, mth, d)
    except ValueError:
        raise BusinessError("errors.invalid_date") from None
    if not (2000 <= result.year <= 2100):
        raise BusinessError("errors.invalid_date")
    return result


def month_start(d: date) -> date:
    return d.replace(day=1)
