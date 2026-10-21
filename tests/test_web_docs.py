"""საბუთების ფორმები რეალური HTTP მოთხოვნებით: მიღება → რეცეპტი → წარმოება → გატანა → ინვენტარიზაცია."""
from sqlalchemy import select

from app.models import Ingredient, Product, Unit


def ids(session_factory):
    with session_factory() as s:
        return {u.code: u.id for u in s.scalars(select(Unit))}


def test_full_flow_through_forms(client, session_factory):
    u = ids(session_factory)
    # ინგრედიენტი სწრაფი შექმნით (საბუთის ფორმიდან)
    r = client.post("/api/ingredients/quick", data={"name": "ფქვილი", "unit_id": str(u["kg"]), "barcode": "4860000000011"})
    assert r.json()["ok"] is True
    flour_id = r.json()["id"]
    dup = client.post("/api/ingredients/quick", data={"name": "ფქვილი", "unit_id": str(u["kg"]), "barcode": ""}).json()
    assert dup["ok"] is False and "name" in dup["errors"]
    # სკანერით ძიება
    assert client.get("/api/ingredients/lookup", params={"q": "4860000000011"}).json()["exact_id"] == flour_id

    # მიღება: ერთი სტრიქონი ფასით, მეორე — თანხით
    r = client.post("/receipts/new", data={
        "date": "10.01.2026", "supplier": "შპს ტესტი", "note": "", "action": "confirm",
        "line_ingredient_id": [str(flour_id), str(flour_id)], "line_qty": ["10", "500"],
        "line_unit_id": [str(u["kg"]), str(u["g"])], "line_price": ["2,50", ""], "line_amount": ["", "1,5"],
        "line_source": ["price", "amount"]}, follow_redirects=False)
    assert r.status_code == 303, r.text[:2000]
    page = client.get(r.headers["location"]).text
    assert "მ-2026-0001" in page and "26,50" in page and "დადასტურდა" in page

    # არასწორი თარიღი და რიცხვი — ორივე შეცდომა ერთად, მონაცემები არ იკარგება
    r = client.post("/receipts/new", data={
        "date": "32.13.2026", "supplier": "", "note": "", "action": "draft",
        "line_ingredient_id": [str(flour_id)], "line_qty": ["abc"], "line_unit_id": [str(u["kg"])],
        "line_price": ["1"], "line_amount": [""], "line_source": ["price"]})
    assert r.status_code == 422 and "დდ.თთ.წწწწ" in r.text and "სწორი რიცხვი" in r.text

    # პროდუქტი და რეცეპტი: 1 პარტია = 10 ცალი, 1 კგ ფქვილი
    r = client.post("/products/new", data={"name": "პური", "unit_id": str(u["pc"]), "code": "", "barcode": "", "note": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        pid = s.scalar(select(Product.id))
    r = client.post("/recipes/new", data={"product_id": str(pid), "yield_qty": "10", "waste_percent": "0", "note": "",
                                          "line_ingredient_id": [str(flour_id)], "line_qty": ["1"],
                                          "line_unit_id": [str(u["kg"])]}, follow_redirects=False)
    assert r.status_code == 303

    # წარმოების გეგმა: 200 ცალი = 20 კგ, ნაშთია 10,5 — აკლია, დადასტურება დაბლოკილია
    plan = client.post("/production/plan", data={"product_id": str(pid), "qty": "200", "date": "11.01.2026"}).text
    assert "აკლია" in plan and "9,5" in plan and "disabled" in plan
    r = client.post("/production/new", data={"product_id": str(pid), "qty": "200", "date": "11.01.2026", "note": "",
                                             "action": "confirm", "plan_key": ""})
    assert r.status_code == 422 and "უარყოფითი" in r.text

    # 50 ცალი — საკმარისია
    r = client.post("/production/new", data={"product_id": str(pid), "qty": "50", "date": "11.01.2026", "note": "",
                                             "action": "confirm", "plan_key": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert "12,62" in client.get(r.headers["location"]).text  # 5 კგ × (26,50 ₾ ÷ 10,5 კგ) — საშუალო შეწონილი

    # გატანა ნაშთზე მეტი — იბლოკება; შემდეგ სწორი
    base = {"date": "12.01.2026", "customer": "მაღაზია", "note": "", "action": "confirm", "line_product_id": [str(pid)]}
    r = client.post("/dispatch/new", data={**base, "line_qty": ["60"], "line_price": ["1"]})
    assert r.status_code == 422 and "აკლია 10" in r.text
    r = client.post("/dispatch/new", data={**base, "line_qty": ["30"], "line_price": ["1,2"]}, follow_redirects=False)
    assert r.status_code == 303

    # ინვენტარიზაცია: ფაქტობრივი ფქვილი 5 კგ (სისტემაში 5,5)
    r = client.post("/inventory/new?type=ingredient", data={"date": "13.01.2026", "note": "", "action": "confirm",
                                                           f"actual_{flour_id}": "5"}, follow_redirects=False)
    assert r.status_code == 303
    assert "-0,5" in client.get(r.headers["location"]).text.replace(" ", " ")

    # რეპორტები და ექსპორტი
    for key in ("ingredient_stock", "product_stock", "movement", "production", "cost", "dispatch"):
        assert client.get(f"/reports/{key}", params={"date_from": "01.01.2026", "date_to": "31.01.2026"}).status_code == 200
    x = client.get("/reports/movement", params={"date_from": "01.01.2026", "date_to": "31.01.2026", "format": "xlsx"})
    assert x.content[:2] == b"PK"
    p = client.get("/reports/production", params={"date_from": "01.01.2026", "date_to": "31.01.2026", "format": "pdf"})
    assert p.content[:4] == b"%PDF"

    # ინგრედიენტი გამოყენებულია → წაშლა შეუძლებელია
    r = client.post(f"/ingredients/{flour_id}/delete", follow_redirects=True)
    assert "ვერ წაიშლება" in r.text
    with session_factory() as s:
        assert s.get(Ingredient, flour_id) is not None

    # პარამეტრები
    r = client.post("/settings/general", data={"cost_method": "last", "production_shortage": "warn"}, follow_redirects=True)
    assert "შენახულია" in r.text
    assert client.get("/").status_code == 200
