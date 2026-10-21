import enum
from decimal import Decimal

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.db_types import ScaledDecimal


class UnitKind(str, enum.Enum):
    MASS = "mass"  # მასა (საბაზო: გ)
    VOLUME = "volume"  # მოცულობა (საბაზო: მლ)
    COUNT = "count"  # ცალობა (საბაზო: ცალი)


def enum_column(enum_cls, length: int = 16):
    """Enum ინახება ტექსტად (value), რომ ბაზაში წასაკითხი იყოს და PostgreSQL-ზეც იმუშაოს."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [x.value for x in e],
        validate_strings=True,
    )


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # სისტემური კოდი: kg, g, l, ml, pc
    name: Mapped[str] = mapped_column(String(32))  # მოკლე: კგ
    full_name: Mapped[str] = mapped_column(String(64))  # სრული: კილოგრამი
    kind: Mapped[UnitKind] = mapped_column(enum_column(UnitKind))
    # რამდენი საბაზო ერთეულია ამ ერთეულში (კგ → 1000 გ)
    factor: Mapped[Decimal] = mapped_column(ScaledDecimal(6))
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<Unit {self.code}>"
