from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Qty
from app.models.units import Unit


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # ნორმალიზებული სახელი ძიებისა და უნიკალურობისთვის (რეგისტრი, მთავრული, ჰარები)
    search_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # ერთეული, რომლითაც ნაშთი ჩანს (მაგ. კგ). რაოდენობები ბაზაში ყოველთვის საბაზო ერთეულშია (გ).
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    min_stock: Mapped[Decimal] = mapped_column(Qty(), default=Decimal(0))  # საბაზო ერთეულში
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    unit: Mapped[Unit] = relationship(lazy="joined")
    barcodes: Mapped[list["IngredientBarcode"]] = relationship(
        back_populates="ingredient",
        cascade="all, delete-orphan",
        order_by="IngredientBarcode.id",
    )

    @property
    def unit_kind(self):
        return self.unit.kind

    def __repr__(self) -> str:
        return f"<Ingredient {self.id} {self.name}>"


class IngredientBarcode(Base):
    __tablename__ = "ingredient_barcodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str] = mapped_column(String(64))  # როგორც შეიყვანეს
    # ნორმალიზებული: ქართული განლაგებით დასკანერებული ასოები → ლათინური, პატარა ასოები
    code_norm: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    ingredient: Mapped[Ingredient] = relationship(back_populates="barcodes")
