"""ძირითადი ბიზნეს-სცენარები: მიღება → წარმოება → გატანა → ინვენტარიზაცია, რედაქტირება და გაუქმება."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.models import DocStatus, ItemType
from app.services import costing, dispatch, inventory, production, products, receipts, recipes, settings, stock
from app.services import ingredients as ing_svc
from app.services.errors import BusinessError, ValidationError

D1, D2, D3 = date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 12)


@pytest.fixture
def setup(db, units):
    flour = ing_svc.create_ingredient(db, ing_svc.IngredientInput(name="ფქვილი", unit_id=units["kg"].id))
    eggs = ing_svc.create_ingredient(db, ing_svc.IngredientInput(name="კვერცხი", unit_id=units["pc"].id))
    bread = products.create_product(db, products.ProductInput(name="პური", unit_id=units["pc"].id))
    # 1 პარტია = 10 ცალი: 2 კგ ფქვილი (500 გ-ით ჩაწერილი 2000 გ) + 4 კვერცხი, ბრაკი 5%
    recipe = recipes.create_recipe(db, bread, recipes.RecipeInput(
        yield_qty=D(10), waste_percent=D(5), lines=[
            recipes.RecipeLineInput(flour.id, D(2000), units["g"].id),
            recipes.RecipeLineInput(eggs.id, D(4), units["pc"].id),
        ]))
    db.commit()
    return {"flour": flour, "eggs": eggs, "bread": bread, "recipe": recipe, "u": units}


def receive(db, s, flour_kg, price, on=D1, eggs=0, confirm=True):
    lines = [receipts.ReceiptLineInput(s["flour"].id, D(flour_kg), s["u"]["kg"].id, unit_price=D(price))]
    if eggs:
        lines.append(receipts.ReceiptLineInput(s["eggs"].id, D(eggs), s["u"]["pc"].id, amount=D("0.5") * eggs,
                                               source="amount"))
    r = receipts.save_receipt(db, receipts.ReceiptInput(date=on, supplier="შპს მომწოდებელი", lines=lines),
                              confirm=confirm)
    db.commit()
    return r


def bal(db, item, t=ItemType.INGREDIENT):
    return stock.balance(db, t, item.id)


def test_receipt_confirm_and_numbering(db, setup):
    r = receive(db, setup, "10", "2.5", eggs=30)
    assert r.number == "მ-2026-0001" and r.status == DocStatus.CONFIRMED
    assert r.total == D("40.00")  # 10×2,5 + 30×0,5
    assert bal(db, setup["flour"]) == D(10000) and bal(db, setup["eggs"]) == D(30)
    assert receive(db, setup, "1", "1", confirm=False).number == "მ-2026-0002"


def test_draft_does_not_affect_stock(db, setup):
    receive(db, setup, "10", "2", confirm=False)
    assert bal(db, setup["flour"]) == 0


def test_production_consumes_with_waste_and_freezes_cost(db, setup):
    receive(db, setup, "10", "2", eggs=30)  # ფქვილი 0,002 ₾/გ, კვერცხი 0,5 ₾
    run = production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(20)),
                              confirm=True)
    db.commit()
    # 20 ცალი = 2 პარტია: ფქვილი 2×2000×1,05 = 4200 გ, კვერცხი 2×4×1,05 = 8,4
    assert bal(db, setup["flour"]) == D(5800) and bal(db, setup["eggs"]) == D("21.6")
    assert bal(db, setup["bread"], ItemType.PRODUCT) == D(20)
    assert run.total_cost == D("12.60")  # 4200×0,002 + 8,4×0,5
    # ფასის შემდგომი ცვლილება დადასტურებულ წარმოებას არ ცვლის
    receive(db, setup, "10", "9", on=D1)
    db.refresh(run)
    assert run.total_cost == D("12.60")


def test_production_blocked_on_shortage_nothing_written(db, setup):
    receive(db, setup, "1", "2", eggs=30)
    with pytest.raises(ValidationError) as e:
        production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(10)),
                            confirm=True)
    assert [m.key for m in e.value.errors["_doc"]] == ["stock.negative"]
    db.rollback()
    assert bal(db, setup["flour"]) == D(1000) and bal(db, setup["bread"], ItemType.PRODUCT) == 0


def test_production_warn_mode_allows_negative(db, setup):
    settings.set_value(db, "production_shortage", "warn")
    production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(10)),
                        confirm=True)
    assert bal(db, setup["flour"]) == D(-2100)


def test_production_actual_quantities_override(db, setup):
    receive(db, setup, "10", "2", eggs=30)
    run = production.save_run(db, production.ProductionInput(
        date=D2, product_id=setup["bread"].id, qty=D(10), actual={setup["flour"].id: D("2.5")}), confirm=True)
    assert bal(db, setup["flour"]) == D(7500)  # ფაქტობრივად 2,5 კგ, რეცეპტით 2,1
    assert run.lines[0].planned_qty_base == D(2100)


def test_edit_confirmed_receipt_cannot_remove_consumed_stock(db, setup):
    r = receive(db, setup, "10", "2", eggs=30)
    production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(30)),
                        confirm=True)
    db.commit()  # დახარჯულია 6300 გ ფქვილი
    smaller = receipts.ReceiptInput(date=D1, lines=[
        receipts.ReceiptLineInput(setup["flour"].id, D(5), setup["u"]["kg"].id, unit_price=D(2)),
        receipts.ReceiptLineInput(setup["eggs"].id, D(30), setup["u"]["pc"].id, unit_price=D("0.5"))])
    with pytest.raises(ValidationError):
        receipts.save_receipt(db, smaller, r)
    db.rollback()
    assert bal(db, setup["flour"]) == D(3700)
    with pytest.raises(ValidationError):
        receipts.cancel_receipt(db, r)
    db.rollback()

    bigger = receipts.ReceiptInput(date=D1, lines=[
        receipts.ReceiptLineInput(setup["flour"].id, D(12), setup["u"]["kg"].id, unit_price=D(2)),
        receipts.ReceiptLineInput(setup["eggs"].id, D(30), setup["u"]["pc"].id, unit_price=D("0.5"))])
    receipts.save_receipt(db, bigger, r)
    db.commit()
    assert bal(db, setup["flour"]) == D(5700)


def test_backdated_consumption_before_receipt_is_blocked(db, setup):
    receive(db, setup, "10", "2", on=D3, eggs=30)
    with pytest.raises(ValidationError):
        production.save_run(db, production.ProductionInput(date=D1, product_id=setup["bread"].id, qty=D(10)),
                            confirm=True)


def test_dispatch_limits_and_cancel_production(db, setup):
    receive(db, setup, "10", "2", eggs=30)
    run = production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(20)),
                              confirm=True)
    db.commit()
    line = dispatch.DispatchLineInput(setup["bread"].id, D(25), D("1.5"))
    with pytest.raises(ValidationError):
        dispatch.save_dispatch(db, dispatch.DispatchInput(date=D3, customer="მაღაზია", lines=[line]), confirm=True)
    db.rollback()
    d = dispatch.save_dispatch(db, dispatch.DispatchInput(
        date=D3, customer="მაღაზია", lines=[dispatch.DispatchLineInput(setup["bread"].id, D(15), D("1.5"))]),
        confirm=True)
    db.commit()
    assert d.total == D("22.50") and bal(db, setup["bread"], ItemType.PRODUCT) == D(5)
    with pytest.raises(ValidationError):  # პროდუქცია უკვე გატანილია
        production.cancel_run(db, run)
    db.rollback()
    dispatch.cancel_dispatch(db, d)
    production.cancel_run(db, run)
    db.commit()
    assert bal(db, setup["flour"]) == D(10000) and bal(db, setup["bread"], ItemType.PRODUCT) == 0


def test_inventory_adjustment(db, setup):
    receive(db, setup, "10", "2")
    c = inventory.save_count(db, inventory.InventoryInput(date=D2, item_type=ItemType.INGREDIENT,
                                                          actual={setup["flour"].id: D("9.25")}), confirm=True)
    db.commit()
    assert c.lines[0].diff_qty_base == D(-750)
    assert bal(db, setup["flour"]) == D(9250)
    inventory.cancel_count(db, c)
    assert bal(db, setup["flour"]) == D(10000)


def test_cost_methods(db, setup):
    receive(db, setup, "10", "2", on=D1)
    receive(db, setup, "10", "4", on=D2)
    fid = setup["flour"].id
    assert costing.ingredient_costs(db, method="average")[fid] == D("0.003")
    assert costing.ingredient_costs(db, method="last")[fid] == D("0.004")
    assert costing.ingredient_costs(db, method="last", as_of=D1)[fid] == D("0.002")


def test_recipe_locked_after_use_and_versions(db, setup):
    receive(db, setup, "10", "2", eggs=30)
    production.save_run(db, production.ProductionInput(date=D2, product_id=setup["bread"].id, qty=D(10)),
                        confirm=True)
    with pytest.raises(BusinessError):
        recipes.update_recipe(db, setup["recipe"], recipes.to_input(setup["recipe"]))
    v2 = recipes.new_version(db, setup["recipe"])
    assert v2.version == 2 and not v2.is_active
    recipes.activate(db, v2)
    assert v2.is_active and not setup["recipe"].is_active
    assert ing_svc.is_ingredient_used(db, setup["flour"].id)
