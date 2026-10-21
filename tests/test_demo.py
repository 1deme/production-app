"""სადემონსტრაციო მონაცემები: ჩატვირთვა მხოლოდ ცარიელზე, გასუფთავება მხოლოდ დემო-რეჟიმში."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Ingredient, Receipt, StockMovement, Unit
from app.services import demo, receipts
from app.services import ingredients as ing_svc
from app.services.errors import BusinessError


def test_load_and_clear(db):
    assert demo.is_empty(db) and not demo.is_demo(db)
    demo.load_demo(db)
    db.commit()
    assert demo.is_demo(db) and not demo.is_empty(db)
    assert db.scalar(select(func.count(StockMovement.id))) > 0
    with pytest.raises(BusinessError):  # მეორედ ჩატვირთვა შეუძლებელია
        demo.load_demo(db)

    demo.clear_demo(db)
    db.commit()
    assert demo.is_empty(db) and not demo.is_demo(db)
    assert db.scalar(select(func.count(Unit.id))) == 5  # ერთეულები რჩება
    # ნომრები თავიდან იწყება
    kg = db.scalar(select(Unit).where(Unit.code == "kg"))
    ing = ing_svc.create_ingredient(db, ing_svc.IngredientInput(name="ფქვილი", unit_id=kg.id))
    r = receipts.save_receipt(db, receipts.ReceiptInput(
        date=Receipt.date.type.python_type.today(),
        lines=[receipts.ReceiptLineInput(ing.id, Decimal(1), kg.id, unit_price=Decimal(1))]))
    assert r.number.endswith("-0001")


def test_clear_refused_for_real_data(db, units):
    ing_svc.create_ingredient(db, ing_svc.IngredientInput(name="რეალური", unit_id=units["kg"].id))
    db.commit()
    with pytest.raises(BusinessError):
        demo.load_demo(db)  # ბაზა ცარიელი არ არის
    with pytest.raises(BusinessError):
        demo.clear_demo(db)  # დემო-რეჟიმი არ არის — რეალური მონაცემები არ იშლება
    assert db.scalar(select(func.count(Ingredient.id))) == 1


def test_web_demo_flow(client):
    home = client.get("/").text
    assert "/demo/load" in home and "სადემონსტრაციო რეჟიმი" not in home
    r = client.post("/demo/load", follow_redirects=True)
    assert "სადემონსტრაციო რეჟიმი" in r.text and "/demo/clear" in r.text
    assert "სადემონსტრაციო რეჟიმი" in client.get("/receipts").text  # ბანერი ყველა გვერდზე
    r = client.post("/demo/clear", follow_redirects=True)
    assert "სადემონსტრაციო რეჟიმი." not in r.text and "/demo/load" in r.text
