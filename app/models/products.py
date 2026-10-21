from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Qty, ScaledDecimal
from app.models.ingredients import Ingredient
from app.models.units import Unit


class Counterparty(Base):
    """მომწოდებლები და კლიენტები (ავტოშევსებისთვის, რომ ერთი სახელი სხვადასხვანაირად არ ჩაიწეროს)."""

    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    search_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    search_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode_norm: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    unit: Mapped[Unit] = relationship(lazy="joined")
    recipes: Mapped[list["Recipe"]] = relationship(back_populates="product", order_by="Recipe.version.desc()")

    @property
    def active_recipe(self) -> "Recipe | None":
        return next((r for r in self.recipes if r.is_active), None)


class Recipe(Base):
    """რეცეპტის ვერსია. პროდუქტს აქვს ერთი აქტიური ვერსია, ძველები ისტორიისთვის ინახება."""

    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("product_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    # გამოსავალი: ერთი პარტიიდან რამდენი პროდუქტი გამოდის (პროდუქტის საბაზო ერთეულში)
    yield_qty: Mapped[Decimal] = mapped_column(Qty())
    # დანაკარგი/ბრაკი %: ემატება ინგრედიენტების ხარჯს (1 კგ + 5% = 1,05 კგ)
    waste_percent: Mapped[Decimal] = mapped_column(ScaledDecimal(2), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    copied_from_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    product: Mapped[Product] = relationship(back_populates="recipes")
    lines: Mapped[list["RecipeLine"]] = relationship(back_populates="recipe", cascade="all, delete-orphan",
                                                     order_by="RecipeLine.position")


class RecipeLine(Base):
    __tablename__ = "recipe_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty())  # როგორც შეიყვანეს (unit-ში)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    qty_base: Mapped[Decimal] = mapped_column(Qty())  # საბაზო ერთეულში, ერთ პარტიაზე

    recipe: Mapped[Recipe] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient] = relationship(lazy="joined")
    unit: Mapped[Unit] = relationship(lazy="joined")
