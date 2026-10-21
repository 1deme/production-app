"""საბუთები: მიღება, წარმოება, გატანა, ინვენტარიზაცია + მოძრაობის ჟურნალი."""
import enum
from datetime import date as date_type, datetime
from decimal import Decimal

from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Money, Price, Qty, UnitCost
from app.models.ingredients import Ingredient
from app.models.products import Counterparty, Product, Recipe
from app.models.units import Unit, enum_column


class DocStatus(str, enum.Enum):
    DRAFT = "draft"  # დრაფტი — ნაშთზე გავლენა არ აქვს
    CONFIRMED = "confirmed"  # დადასტურებული — ნაშთი შეცვლილია
    CANCELLED = "cancelled"  # გაუქმებული (სტორნო) — მოძრაობები წაშლილია


class ItemType(str, enum.Enum):
    INGREDIENT = "ingredient"
    PRODUCT = "product"


class MovementType(str, enum.Enum):
    RECEIPT = "receipt"  # მიღება
    PRODUCTION_USE = "production_use"  # წარმოებაში ხარჯი
    PRODUCTION_OUTPUT = "production_output"  # წარმოების გამოშვება
    DISPATCH = "dispatch"  # გატანა
    ADJUSTMENT = "adjustment"  # კორექტირება / ინვენტარიზაცია


class DocumentSequence(Base):
    """საბუთების ნომრების მთვლელი: ტიპი + წელი."""

    __tablename__ = "document_sequences"

    doc_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class _DocMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    status: Mapped[DocStatus] = mapped_column(enum_column(DocStatus), default=DocStatus.DRAFT, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- მიღება

class Receipt(_DocMixin, Base):
    __tablename__ = "receipts"

    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    counterparty: Mapped[Counterparty | None] = relationship(lazy="joined")
    lines: Mapped[list["ReceiptLine"]] = relationship(back_populates="receipt", cascade="all, delete-orphan",
                                                      order_by="ReceiptLine.position")

    @property
    def total(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal("0.00"))


class ReceiptLine(Base):
    __tablename__ = "receipt_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty())  # შეყვანილ ერთეულში
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    qty_base: Mapped[Decimal] = mapped_column(Qty())
    unit_price: Mapped[Decimal] = mapped_column(Price())  # შეყვანილი ერთეულის ფასი
    amount: Mapped[Decimal] = mapped_column(Money())

    receipt: Mapped[Receipt] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient] = relationship(lazy="joined")
    unit: Mapped[Unit] = relationship(lazy="joined")


# ---------------------------------------------------------------- წარმოება

class ProductionRun(_DocMixin, Base):
    __tablename__ = "production_runs"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty())  # გამოშვებული რაოდენობა
    total_cost: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0))
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost(), default=Decimal(0))  # საბაზო ერთეულზე

    product: Mapped[Product] = relationship(lazy="joined")
    recipe: Mapped[Recipe | None] = relationship()
    lines: Mapped[list["ProductionLine"]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                         order_by="ProductionLine.position")


class ProductionLine(Base):
    """ფაქტობრივად ჩამოწერილი ინგრედიენტი — რეცეპტის შემდგომი ცვლილება ამას არ ცვლის."""

    __tablename__ = "production_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty())
    planned_qty_base: Mapped[Decimal] = mapped_column(Qty())  # რეცეპტით გამოთვლილი
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost(), default=Decimal(0))
    amount: Mapped[Decimal] = mapped_column(Money(), default=Decimal(0))

    run: Mapped[ProductionRun] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient] = relationship(lazy="joined")


# ---------------------------------------------------------------- გატანა

class Dispatch(_DocMixin, Base):
    __tablename__ = "dispatches"

    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    counterparty: Mapped[Counterparty | None] = relationship(lazy="joined")
    lines: Mapped[list["DispatchLine"]] = relationship(back_populates="dispatch", cascade="all, delete-orphan",
                                                       order_by="DispatchLine.position")

    @property
    def total(self) -> Decimal:
        return sum((ln.amount or Decimal(0) for ln in self.lines), Decimal("0.00"))


class DispatchLine(Base):
    __tablename__ = "dispatch_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dispatch_id: Mapped[int] = mapped_column(ForeignKey("dispatches.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty())
    unit_price: Mapped[Decimal | None] = mapped_column(Price(), nullable=True)  # პროდუქტის ერთეულზე
    amount: Mapped[Decimal | None] = mapped_column(Money(), nullable=True)

    dispatch: Mapped[Dispatch] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(lazy="joined")


# ---------------------------------------------------------------- ინვენტარიზაცია

class InventoryCount(_DocMixin, Base):
    __tablename__ = "inventory_counts"

    item_type: Mapped[ItemType] = mapped_column(enum_column(ItemType))
    lines: Mapped[list["InventoryLine"]] = relationship(back_populates="count", cascade="all, delete-orphan",
                                                        order_by="InventoryLine.position")


class InventoryLine(Base):
    __tablename__ = "inventory_lines"
    __table_args__ = (CheckConstraint("(ingredient_id IS NULL) <> (product_id IS NULL)", name="one_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inventory_counts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), nullable=True, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    actual_qty_base: Mapped[Decimal] = mapped_column(Qty())
    system_qty_base: Mapped[Decimal | None] = mapped_column(Qty(), nullable=True)  # ივსება დადასტურებისას
    diff_qty_base: Mapped[Decimal | None] = mapped_column(Qty(), nullable=True)

    count: Mapped[InventoryCount] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient | None] = relationship(lazy="joined")
    product: Mapped[Product | None] = relationship(lazy="joined")

    @property
    def item(self):
        return self.ingredient or self.product


# ---------------------------------------------------------------- მოძრაობის ჟურნალი

class StockMovement(Base):
    """ნაშთის ყოველი ცვლილება. ნაშთი = ამ ცხრილის ჯამი (ყოველთვის ითვლება აქედან)."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("(ingredient_id IS NULL) <> (product_id IS NULL)", name="one_item"),
        Index("ix_stock_movements_doc", "doc_type", "doc_id"),
        Index("ix_stock_movements_ing_date", "ingredient_id", "date"),
        Index("ix_stock_movements_prod_date", "product_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    item_type: Mapped[ItemType] = mapped_column(enum_column(ItemType))
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    movement_type: Mapped[MovementType] = mapped_column(enum_column(MovementType, 24))
    qty: Mapped[Decimal] = mapped_column(Qty())  # +/- საბაზო ერთეულში
    unit_cost: Mapped[Decimal | None] = mapped_column(UnitCost(), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Money(), nullable=True)  # +/- ღირებულება
    doc_type: Mapped[str] = mapped_column(String(16))
    doc_id: Mapped[int] = mapped_column(Integer)
    doc_number: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    ingredient: Mapped[Ingredient | None] = relationship()
    product: Mapped[Product | None] = relationship()
