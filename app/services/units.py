"""საზომი ერთეულები და კონვერტაცია.

კონვერტაცია შესაძლებელია მხოლოდ ერთი ტიპის (მასა / მოცულობა / ცალობა) ფარგლებში.
ბაზაში ყველა რაოდენობა ინახება საბაზო ერთეულში: გ, მლ, ცალი.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db_types import q_qty, to_decimal
from app.i18n import t
from app.models.units import Unit, UnitKind
from app.services.errors import BusinessError

# საწყისი ერთეულები: (კოდი, მოკლე, სრული, ტიპი, კოეფიციენტი, საბაზოა?, რიგი)
DEFAULT_UNITS = [
    ("kg", "კგ", "კილოგრამი", UnitKind.MASS, Decimal(1000), False, 10),
    ("g", "გ", "გრამი", UnitKind.MASS, Decimal(1), True, 11),
    ("l", "ლ", "ლიტრი", UnitKind.VOLUME, Decimal(1000), False, 20),
    ("ml", "მლ", "მილილიტრი", UnitKind.VOLUME, Decimal(1), True, 21),
    ("pc", "ცალი", "ცალი", UnitKind.COUNT, Decimal(1), True, 30),
]


def ensure_default_units(session: Session) -> None:
    """ერთეულების შექმნა/განახლება (იდემპოტენტური). კოეფიციენტები არ იცვლება არსებულზე."""
    existing = {u.code: u for u in session.scalars(select(Unit))}
    for code, name, full, kind, factor, is_base, order in DEFAULT_UNITS:
        unit = existing.get(code)
        if unit is None:
            session.add(Unit(code=code, name=name, full_name=full, kind=kind,
                             factor=factor, is_base=is_base, sort_order=order))
        else:
            unit.name, unit.full_name, unit.sort_order = name, full, order
    session.flush()


def all_units(session: Session) -> list[Unit]:
    return list(session.scalars(select(Unit).order_by(Unit.sort_order)))


def compatible_units(session: Session, kind: UnitKind) -> list[Unit]:
    return list(session.scalars(select(Unit).where(Unit.kind == kind).order_by(Unit.sort_order)))


def kind_label(kind: UnitKind) -> str:
    return t(f"unit_kind.{kind.value}")


def ensure_compatible(a: Unit, b: Unit) -> None:
    if a.kind != b.kind:
        raise BusinessError(
            "errors.incompatible_units",
            a=a.name, b=b.name, kind_a=kind_label(a.kind), kind_b=kind_label(b.kind),
        )


def to_base(qty, unit: Unit) -> Decimal:
    """რაოდენობა ერთეულიდან → საბაზო ერთეულში (მაგ. 1,5 კგ → 1500 გ)."""
    return q_qty(to_decimal(qty) * unit.factor)


def from_base(qty_base, unit: Unit) -> Decimal:
    """საბაზო ერთეულიდან → მოცემულ ერთეულში (მაგ. 1500 გ → 1,5 კგ). ზუსტი, დამრგვალების გარეშე."""
    return to_decimal(qty_base) / unit.factor


def convert(qty, from_unit: Unit, to_unit: Unit) -> Decimal:
    ensure_compatible(from_unit, to_unit)
    return to_decimal(qty) * from_unit.factor / to_unit.factor
