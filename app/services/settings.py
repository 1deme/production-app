"""პროგრამის პარამეტრები ბაზაში (settings ცხრილი).

ყველა პარამეტრს აქვს ნაგულისხმევი მნიშვნელობა, ამიტომ ცარიელ ბაზაზეც სწორად მუშაობს.
"""
from sqlalchemy.orm import Session

from app.models.system import Setting

# key → (ნაგულისხმევი, დასაშვები მნიშვნელობები)
OPTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "cost_method": ("average", ("average", "last")),
    "production_shortage": ("block", ("block", "warn")),
}
DEFAULTS: dict[str, str] = {k: v[0] for k, v in OPTIONS.items()}


def get(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key)


def get_bool(session: Session, key: str) -> bool:
    return (get(session, key) or "").lower() in ("1", "true", "yes")


def set_value(session: Session, key: str, value: str) -> None:
    if key in OPTIONS and value not in OPTIONS[key][1]:
        raise ValueError(f"დაუშვებელი მნიშვნელობა {key}={value}")
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.flush()


def all_values(session: Session) -> dict[str, str]:
    return {k: get(session, k) for k in OPTIONS}
