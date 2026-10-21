"""ერთეულების კონვერტაცია, რიცხვების წაკითხვა/ფორმატირება, Decimal-ის შენახვა."""
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, func, insert, select

from app.db_types import Qty, ScaledDecimal
from app.formatting import fmt_date, fmt_money, fmt_qty, format_number, input_value
from app.services.errors import BusinessError
from app.services.numbers import parse_decimal
from app.services.units import convert, from_base, to_base


# ---------------------------------------------------------------- კონვერტაცია

def test_to_base_and_back(units):
    assert to_base(Decimal("1.5"), units["kg"]) == Decimal("1500")
    assert to_base(Decimal("250"), units["ml"]) == Decimal("250")
    assert from_base(Decimal("1500"), units["kg"]) == Decimal("1.5")
    assert from_base(Decimal("125"), units["kg"]) == Decimal("0.125")


def test_convert_same_kind(units):
    assert convert(Decimal("2"), units["l"], units["ml"]) == Decimal("2000")
    assert convert(Decimal("750"), units["g"], units["kg"]) == Decimal("0.75")


@pytest.mark.parametrize("a,b", [("kg", "l"), ("pc", "g"), ("ml", "pc")])
def test_convert_incompatible_kinds_rejected(units, a, b):
    with pytest.raises(BusinessError) as e:
        convert(Decimal("1"), units[a], units[b])
    assert e.value.msg.key == "errors.incompatible_units"


def test_to_base_rounds_to_storage_precision(units):
    # 0,0000004 კგ = 0,0004 გ → ინახება 3 ათწილადამდე (მგ)
    assert to_base(Decimal("1.0000004"), units["kg"]) == Decimal("1000.000")


def test_float_is_rejected(units):
    with pytest.raises(TypeError):
        to_base(1.5, units["kg"])


# ---------------------------------------------------------------- რიცხვების წაკითხვა

@pytest.mark.parametrize("raw,expected", [
    ("12,5", "12.5"), ("12.5", "12.5"), ("1 234,50", "1234.50"), ("1 234,5", "1234.5"),
    ("1.234,5", "1234.5"), ("1,234.5", "1234.5"), ("0,125", "0.125"), (",5", "0.5"),
    ("10 ₾", "10"), ("  7  ", "7"), ("1,234", "1.234"),
])
def test_parse_decimal_formats(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "1,2,3", "1.2.3", "12a", "1.23,4.5", "--1", "1 2,3.4"])
def test_parse_decimal_invalid(raw):
    with pytest.raises(BusinessError):
        parse_decimal(raw)


def test_parse_decimal_rules():
    assert parse_decimal("") is None
    with pytest.raises(BusinessError) as e:
        parse_decimal("", required=True)
    assert e.value.msg.key == "common.required"
    with pytest.raises(BusinessError) as e:
        parse_decimal("-5")
    assert e.value.msg.key == "errors.negative_not_allowed"
    assert parse_decimal("-5", allow_negative=True) == Decimal(-5)
    with pytest.raises(BusinessError) as e:
        parse_decimal("1,23456", places=3)
    assert e.value.msg.key == "errors.too_many_decimals"
    assert parse_decimal("1,2300", places=2) == Decimal("1.23")  # ბოლო ნულები არ ითვლება


# ---------------------------------------------------------------- ფორმატირება

def test_formatting():
    assert fmt_money(Decimal("1234.5")) == "1 234,50 ₾"
    assert fmt_money(Decimal("0")) == "0,00 ₾"
    assert fmt_money(Decimal("-12.345")) == "-12,35 ₾"
    assert format_number(Decimal("1234567.891"), 0, 2) == "1 234 567,89"
    assert input_value(Decimal("1234.500")) == "1234,5"
    from datetime import date
    assert fmt_date(date(2026, 3, 7)) == "07.03.2026"


def test_fmt_qty_uses_display_unit(units):
    assert fmt_qty(Decimal("1500"), units["kg"]) == "1,5 კგ"
    assert fmt_qty(Decimal("125"), units["kg"]) == "0,125 კგ"
    assert fmt_qty(Decimal("12"), units["pc"]) == "12 ცალი"


# ---------------------------------------------------------------- ზუსტი შენახვა ბაზაში

def test_scaled_decimal_roundtrip_and_exact_sum(session_factory):
    md = MetaData()
    tbl = Table("tmp_qty", md, Column("id", Integer, primary_key=True), Column("q", Qty()))
    engine = session_factory.kw["bind"]
    md.create_all(engine)
    values = [Decimal("0.1"), Decimal("0.2"), Decimal("1234.567"), Decimal("-0.001")]
    with engine.begin() as conn:
        conn.execute(insert(tbl), [{"q": v} for v in values])
        rows = [r[0] for r in conn.execute(select(tbl.c.q).order_by(tbl.c.id))]
        total = conn.execute(select(func.sum(tbl.c.q))).scalar_one()
    assert rows == values
    assert all(isinstance(r, Decimal) for r in rows)
    assert total == Decimal("1234.866")  # float-ით 0.1 + 0.2 ≠ 0.3 — აქ ზუსტია
    assert isinstance(total, Decimal)


def test_scaled_decimal_rounding():
    t = ScaledDecimal(2)
    assert t.process_bind_param(Decimal("1.005"), None) == 101  # ROUND_HALF_UP
    assert t.process_result_value(101, None) == Decimal("1.01")
