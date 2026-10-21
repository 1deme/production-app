"""სადემონსტრაციო მონაცემები:  python seed.py

ამატებს ინგრედიენტებს, 3 პროდუქტს რეცეპტებით და რამდენიმე საბუთს (მიღება, წარმოება, გატანა).
უსაფრთხოა რამდენჯერმე გაშვება: არსებულ მონაცემებს არ ცვლის, საბუთებს კი ქმნის მხოლოდ ცარიელ ბაზაზე.
"""
import sys
from datetime import date, timedelta
from decimal import Decimal as D

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.bootstrap import prepare_database  # noqa: E402
from app.db import get_session_factory  # noqa: E402
from app.models import Ingredient, Product, Receipt, Unit  # noqa: E402
from app.services import dispatch, production, products, receipts, recipes  # noqa: E402
from app.services import ingredients as ingredient_svc  # noqa: E402
from app.services.text import normalize_text  # noqa: E402

# (დასახელება, ერთეული, მინ. ნაშთი ამ ერთეულში, შტრიხკოდები)
INGREDIENTS = [
    ("ხორბლის ფქვილი (უმაღლესი ხარისხი)", "kg", "50", ["4860001234567", "4860001234574"]),
    ("შაქარი", "kg", "20", ["4860019876543"]),
    ("კარაქი 82%", "kg", "5", ["4860105550012"]),
    ("კვერცხი", "pc", "120", ["4860200000017"]),
    ("რძე 3,2%", "l", "10", ["4860300000116"]),
    ("საფუარი (მშრალი)", "g", "500", ["5900000123457"]),
    ("მარილი", "kg", "2", []),
    ("ვანილის შაქარი", "g", "200", ["4000400140137"]),
    ("კაკაოს ფხვნილი", "kg", "1", ["8710400211116"]),
    ("მზესუმზირის ზეთი", "l", "5", ["4860400501231"]),
    ("იმერული ყველი", "kg", "15", []),
    ("ნაღები 33%", "ml", "2000", ["4860300000222"]),
]

FLOUR, SUGAR, BUTTER, EGGS, MILK, YEAST, SALT, VANILLA, COCOA, OIL, CHEESE, CREAM = (i[0] for i in INGREDIENTS)

# (პროდუქტი, ერთეული, კოდი, გამოსავალი, ბრაკი %, [(ინგრედიენტი, რაოდენობა, ერთეული)])
PRODUCTS = [
    ("თეთრი პური", "pc", "P-001", "20", "3", [
        (FLOUR, "10", "kg"), (YEAST, "100", "g"), (SALT, "200", "g"), (OIL, "0,3", "l")]),
    ("იმერული ხაჭაპური", "pc", "P-002", "10", "2", [
        (FLOUR, "3", "kg"), (CHEESE, "4", "kg"), (EGGS, "5", "pc"), (BUTTER, "300", "g"),
        (MILK, "1", "l"), (YEAST, "30", "g"), (SALT, "30", "g")]),
    ("შოკოლადის ნამცხვარი", "pc", "P-003", "8", "5", [
        (FLOUR, "1,2", "kg"), (SUGAR, "1", "kg"), (EGGS, "12", "pc"), (BUTTER, "800", "g"),
        (COCOA, "250", "g"), (CREAM, "500", "ml"), (VANILLA, "20", "g")]),
]

# (ინგრედიენტი, რაოდენობა, ერთეული, ფასი ერთეულზე)
RECEIPT_1 = [(FLOUR, "100", "kg", "1,85"), (SUGAR, "50", "kg", "2,40"), (BUTTER, "20", "kg", "18,50"),
             (EGGS, "360", "pc", "0,38"), (MILK, "30", "l", "3,20"), (YEAST, "1000", "g", "0,045"),
             (SALT, "10", "kg", "0,90")]
RECEIPT_2 = [(COCOA, "3", "kg", "24,00"), (VANILLA, "500", "g", "0,06"), (OIL, "10", "l", "5,40"),
             (CHEESE, "30", "kg", "16,00"), (CREAM, "5000", "ml", "0,012"), (FLOUR, "50", "kg", "1,95")]


def _d(s: str) -> D:
    return D(s.replace(",", "."))


def seed_ingredients(session: Session) -> int:
    units = {u.code: u for u in session.scalars(select(Unit))}
    added = 0
    for name, unit_code, min_stock, barcodes in INGREDIENTS:
        if session.scalar(select(Ingredient).where(Ingredient.search_name == normalize_text(name))):
            continue
        ingredient_svc.create_ingredient(session, ingredient_svc.IngredientInput(
            name=name, unit_id=units[unit_code].id, min_stock=_d(min_stock), barcodes=barcodes))
        added += 1
    session.flush()
    return added


def seed_products(session: Session) -> int:
    units = {u.code: u for u in session.scalars(select(Unit))}
    ings = {i.name: i for i in session.scalars(select(Ingredient))}
    added = 0
    for name, unit_code, code, yield_qty, waste, lines in PRODUCTS:
        if session.scalar(select(Product).where(Product.search_name == normalize_text(name))):
            continue
        p = products.create_product(session, products.ProductInput(name=name, unit_id=units[unit_code].id, code=code))
        recipes.create_recipe(session, p, recipes.RecipeInput(
            yield_qty=_d(yield_qty), waste_percent=_d(waste),
            lines=[recipes.RecipeLineInput(ings[i].id, _d(q), units[u].id) for i, q, u in lines]))
        added += 1
    session.flush()
    return added


def seed_documents(session: Session) -> bool:
    if session.scalar(select(func.count(Receipt.id))):
        return False  # საბუთები უკვე არსებობს — არ ვამრავლებთ
    units = {u.code: u for u in session.scalars(select(Unit))}
    ings = {i.name: i for i in session.scalars(select(Ingredient))}
    prods = {p.name: p for p in session.scalars(select(Product))}
    today = date.today()

    def receipt(lines, supplier, days_ago):
        receipts.save_receipt(session, receipts.ReceiptInput(
            date=today - timedelta(days=days_ago), supplier=supplier,
            lines=[receipts.ReceiptLineInput(ings[i].id, _d(q), units[u].id, unit_price=_d(p)) for i, q, u, p in lines]),
            confirm=True)

    receipt(RECEIPT_1, "შპს „ფქვილის სახლი“", 12)
    receipt(RECEIPT_2, "ი/მ გიორგი ბერიძე", 9)
    for days_ago, name, qty in [(8, "თეთრი პური", "60"), (7, "იმერული ხაჭაპური", "30"), (6, "შოკოლადის ნამცხვარი", "8"),
                                (4, "თეთრი პური", "40"), (3, "იმერული ხაჭაპური", "20"), (1, "თეთრი პური", "40")]:
        production.save_run(session, production.ProductionInput(
            date=today - timedelta(days=days_ago), product_id=prods[name].id, qty=_d(qty)), confirm=True)
    for days_ago, customer, lines in [
        (6, "მაღაზია №1", [("თეთრი პური", "40", "1,20"), ("იმერული ხაჭაპური", "15", "9,00")]),
        (2, "კაფე „დილა“", [("შოკოლადის ნამცხვარი", "5", "38,00"), ("იმერული ხაჭაპური", "20", "9,50")]),
        (0, "მაღაზია №1", [("თეთრი პური", "70", "1,20")]),
    ]:
        dispatch.save_dispatch(session, dispatch.DispatchInput(
            date=today - timedelta(days=days_ago), customer=customer,
            lines=[dispatch.DispatchLineInput(prods[n].id, _d(q), _d(p)) for n, q, p in lines]), confirm=True)
    # ერთი დრაფტი — რომ ჩანდეს, როგორ გამოიყურება დაუდასტურებელი საბუთი
    receipts.save_receipt(session, receipts.ReceiptInput(
        date=today, supplier="შპს „ფქვილის სახლი“",
        lines=[receipts.ReceiptLineInput(ings[FLOUR].id, D(50), units["kg"].id, unit_price=D("1.90"))]))
    return True


def main() -> None:
    prepare_database()
    session = get_session_factory()()
    try:
        n_ing = seed_ingredients(session)
        n_prod = seed_products(session)
        docs = seed_documents(session)
        session.commit()
        print(f"დაემატა ინგრედიენტი: {n_ing}, პროდუქტი რეცეპტით: {n_prod}")
        print("სადემონსტრაციო საბუთები შეიქმნა." if docs else "საბუთები უკვე არსებობს — ახალი არ დამატებულა.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
