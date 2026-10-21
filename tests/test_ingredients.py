"""ინგრედიენტები: ვალიდაცია, დუბლიკატები, შტრიხკოდები, ძიება, ჟურნალი."""
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AuditLog, IngredientBarcode
from app.services import ingredients as svc
from app.services.errors import BusinessError, ValidationError
from app.services.text import barcode_key, looks_like_barcode, normalize_text


def make(db, units, name, unit="kg", barcodes=(), min_stock="0"):
    ing = svc.create_ingredient(db, svc.IngredientInput(
        name=name, unit_id=units[unit].id, min_stock=Decimal(min_stock), barcodes=list(barcodes)))
    db.commit()
    return ing


def errors_of(exc: ValidationError) -> dict:
    return {k: [m.key for m in v] for k, v in exc.errors.items()}


# ---------------------------------------------------------------- ნორმალიზაცია

def test_normalize_georgian_mtavruli_and_spaces():
    assert normalize_text("  ᲤᲥᲕᲘᲚᲘ   უმაღლესი ") == "ფქვილი უმაღლესი"
    assert normalize_text("Coca  COLA") == "coca cola"


def test_barcode_key_handles_georgian_keyboard_layout():
    # სკანერმა „AB-12“ ქართული განლაგებით „აბ-12“-ად აკრიფა
    assert barcode_key("აბ-12") == barcode_key("AB-12") == "ab-12"
    assert barcode_key(" 4860 0012\t34567 ") == "4860001234567"


def test_looks_like_barcode():
    assert looks_like_barcode("4860001234567")
    assert not looks_like_barcode("შაქარი")
    assert not looks_like_barcode("12")


# ---------------------------------------------------------------- შექმნა / ვალიდაცია

def test_create_stores_min_stock_in_base_unit(db, units):
    ing = make(db, units, "ფქვილი", "kg", ["4860001234567"], min_stock="12,5".replace(",", "."))
    assert ing.min_stock == Decimal("12500")
    assert [b.barcode for b in ing.barcodes] == ["4860001234567"]
    assert ing.search_name == "ფქვილი"


def test_required_fields(db, units):
    with pytest.raises(ValidationError) as e:
        svc.create_ingredient(db, svc.IngredientInput(name="   ", unit_id=None))
    assert errors_of(e.value) == {"name": ["common.required"], "unit_id": ["errors.unknown_unit"]}


@pytest.mark.parametrize("dup", ["შაქარი", "  შაქარი ", "ᲨᲐᲥᲐᲠᲘ", "შაქარი"])
def test_duplicate_name_rejected_regardless_of_case_and_spaces(db, units, dup):
    make(db, units, "შაქარი")
    with pytest.raises(ValidationError) as e:
        svc.create_ingredient(db, svc.IngredientInput(name=dup, unit_id=units["kg"].id))
    assert errors_of(e.value)["name"] == ["ingredients.duplicate_name"]


def test_duplicate_barcode_rejected_across_ingredients(db, units):
    make(db, units, "შაქარი", barcodes=["4860019876543"])
    with pytest.raises(ValidationError) as e:
        svc.create_ingredient(db, svc.IngredientInput(name="მარილი", unit_id=units["kg"].id,
                                                      barcodes=[" 4860019876543 "]))
    assert errors_of(e.value)["barcodes"] == ["ingredients.duplicate_barcode"]


def test_duplicate_barcode_inside_one_form(db, units):
    with pytest.raises(ValidationError) as e:
        svc.create_ingredient(db, svc.IngredientInput(name="მარილი", unit_id=units["kg"].id,
                                                      barcodes=["AB12", "ab12", ""]))
    assert errors_of(e.value)["barcodes"] == ["ingredients.duplicate_barcode_in_form"]


def test_multiple_barcodes_and_empty_rows_ignored(db, units):
    ing = make(db, units, "კარაქი", barcodes=["111111", "", "  ", "222222"])
    assert sorted(b.barcode for b in ing.barcodes) == ["111111", "222222"]


def test_negative_min_stock_rejected(db, units):
    with pytest.raises(ValidationError) as e:
        svc.create_ingredient(db, svc.IngredientInput(name="x", unit_id=units["kg"].id, min_stock=Decimal(-1)))
    assert "min_stock" in e.value.errors


# ---------------------------------------------------------------- რედაქტირება

def test_update_syncs_barcodes_and_allows_own_name(db, units):
    ing = make(db, units, "რძე", "l", ["111111", "222222"])
    svc.update_ingredient(db, ing, svc.IngredientInput(
        name="რძე", unit_id=units["l"].id, min_stock=Decimal(5), barcodes=["222222", "333333"]))
    db.commit()
    assert sorted(b.barcode for b in ing.barcodes) == ["222222", "333333"]
    assert ing.min_stock == Decimal("5000")
    remaining = db.scalars(select(IngredientBarcode.barcode)).all()
    assert sorted(remaining) == ["222222", "333333"]  # ძველი შტრიხკოდი წაიშალა ბაზიდანაც


def test_update_writes_audit_diff_only_when_changed(db, units):
    ing = make(db, units, "მარილი", "kg")
    same = svc.IngredientInput(name="მარილი", unit_id=units["kg"].id)
    svc.update_ingredient(db, ing, same)
    db.commit()
    assert db.scalars(select(AuditLog.action)).all() == ["create"]  # ცარიელი ცვლილება არ ჩაიწერა

    svc.update_ingredient(db, ing, svc.IngredientInput(name="მარილი (იოდირებული)", unit_id=units["g"].id,
                                                       min_stock=Decimal(500)))
    db.commit()
    entry = db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    changes = json.loads(entry.changes)
    assert entry.action == "update"
    assert changes["name"] == ["მარილი", "მარილი (იოდირებული)"]
    assert changes["unit"] == ["კგ", "გ"]
    assert changes["min_stock"] == ["0 კგ", "500 გ"]


def test_unit_kind_locked_when_used(db, units, monkeypatch):
    ing = make(db, units, "კვერცხი", "pc")
    monkeypatch.setattr(svc, "is_ingredient_used", lambda s, i: True)
    with pytest.raises(ValidationError) as e:
        svc.update_ingredient(db, ing, svc.IngredientInput(name="კვერცხი", unit_id=units["kg"].id))
    assert errors_of(e.value)["unit_id"] == ["ingredients.unit_kind_locked"]


def test_unit_change_within_kind_allowed_when_used(db, units, monkeypatch):
    ing = make(db, units, "ფქვილი", "kg")
    monkeypatch.setattr(svc, "is_ingredient_used", lambda s, i: True)
    svc.update_ingredient(db, ing, svc.IngredientInput(name="ფქვილი", unit_id=units["g"].id))
    assert ing.unit.code == "g"


# ---------------------------------------------------------------- სტატუსი / წაშლა

def test_deactivate_and_delete(db, units, monkeypatch):
    ing = make(db, units, "ვანილი", "g", ["999999"])
    svc.set_active(db, ing, False)
    db.commit()
    assert not ing.is_active
    assert svc.search_ingredients(db, "ვანილი", "active").items == []
    assert len(svc.search_ingredients(db, "ვანილი", "inactive").items) == 1

    monkeypatch.setattr(svc, "is_ingredient_used", lambda s, i: True)
    with pytest.raises(BusinessError):
        svc.delete_ingredient(db, ing)
    monkeypatch.setattr(svc, "is_ingredient_used", lambda s, i: False)
    svc.delete_ingredient(db, ing)
    db.commit()
    assert db.scalars(select(IngredientBarcode)).all() == []
    actions = db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == ["create", "deactivate", "delete"]


# ---------------------------------------------------------------- ძიება

@pytest.fixture
def catalog(db, units):
    make(db, units, "ხორბლის ფქვილი (უმაღლესი)", "kg", ["4860001234567"])
    make(db, units, "სიმინდის ფქვილი", "kg", ["4860009999999"])
    make(db, units, "შაქარი", "kg", ["4860019876543"])
    make(db, units, "100% ნატურალური წვენი", "l", ["AB-12"])
    make(db, units, "ფქვილი_სპეციალური", "kg")


def names(result):
    return [i.name for i in result.items]


def test_search_partial_name_case_insensitive(db, catalog):
    assert sorted(names(svc.search_ingredients(db, "ფქვ"))) == [
        "სიმინდის ფქვილი", "ფქვილი_სპეციალური", "ხორბლის ფქვილი (უმაღლესი)"]
    assert names(svc.search_ingredients(db, "ᲨᲐᲥ")) == ["შაქარი"]


def test_search_multiple_words_any_order(db, catalog):
    assert names(svc.search_ingredients(db, "უმაღლ ხორბ")) == ["ხორბლის ფქვილი (უმაღლესი)"]


def test_search_by_full_and_partial_barcode(db, catalog):
    r = svc.search_ingredients(db, "4860019876543")
    assert names(r) == ["შაქარი"] and [i.name for i in r.exact_barcode] == ["შაქარი"]
    r = svc.search_ingredients(db, "98765")
    assert names(r) == ["შაქარი"] and r.exact_barcode == []


def test_search_barcode_scanned_with_georgian_layout(db, catalog):
    r = svc.search_ingredients(db, "აბ-12")
    assert [i.name for i in r.exact_barcode] == ["100% ნატურალური წვენი"]


def test_search_like_wildcards_are_literal(db, catalog):
    assert names(svc.search_ingredients(db, "100%")) == ["100% ნატურალური წვენი"]
    assert names(svc.search_ingredients(db, "_")) == ["ფქვილი_სპეციალური"]


def test_search_exact_barcode_ranked_first(db, units, catalog):
    make(db, units, "4860009999999 — სახელში შტრიხკოდით", "kg")
    r = svc.search_ingredients(db, "4860009999999")
    assert r.items[0].name == "სიმინდის ფქვილი"


def test_search_empty_returns_all_sorted(db, catalog):
    r = svc.search_ingredients(db, "")
    assert r.total == 5
    assert names(r) == sorted(names(r), key=normalize_text)
