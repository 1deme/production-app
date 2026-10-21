"""ზუსტი ათწილადი რიცხვების შენახვა ბაზაში.

SQLite-ში Numeric სინამდვილეში float-ად ინახება და სიზუსტე იკარგება.
ამიტომ ყველა რაოდენობა და თანხა ინახება მთელ რიცხვად, მასშტაბირებულად:
მაგ. Qty (3 ათწილადი): 1234,5 გ → 1234500. Python-ში ყოველთვის Decimal ბრუნდება.
SUM() SQL-შიც ზუსტი რჩება, ხოლო PostgreSQL-ზე იგივე სქემა უცვლელად მუშაობს.
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator


def to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # float-ის შემთხვევითი გამოყენება შეცდომაა — ვიჭერთ დაუყოვნებლივ.
        raise TypeError("float არ გამოიყენება ფულად/რაოდენობრივ მნიშვნელობებში, გამოიყენეთ Decimal")
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"Decimal-ად ვერ გარდაიქმნება: {value!r}")


class ScaledDecimal(TypeDecorator):
    """Decimal, რომელიც ინახება BIGINT-ად 10^places მასშტაბით."""

    impl = BigInteger
    cache_ok = True

    def __init__(self, places: int):
        super().__init__()
        self.places = places
        self.quantum = Decimal(1).scaleb(-places)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = to_decimal(value).quantize(self.quantum, rounding=ROUND_HALF_UP)
        return int(d.scaleb(self.places))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(int(value)).scaleb(-self.places).quantize(self.quantum)

    def coerce_compared_value(self, op, value):
        return self


QTY_PLACES = 3  # საბაზო ერთეულის მეათასედი (მგ / მკლ / 0,001 ცალი)
PRICE_PLACES = 4  # ერთეულის ფასი, როგორც შეიყვანეს (მაგ. 2,3450 ₾/კგ)
MONEY_PLACES = 2  # ჯამური თანხები — თეთრამდე
UNIT_COST_PLACES = 8  # თვითღირებულება საბაზო ერთეულზე (მაგ. ₾/გ)


def Qty():
    return ScaledDecimal(QTY_PLACES)


def Price():
    return ScaledDecimal(PRICE_PLACES)


def Money():
    return ScaledDecimal(MONEY_PLACES)


def UnitCost():
    return ScaledDecimal(UNIT_COST_PLACES)


def quantum(places: int) -> Decimal:
    return Decimal(1).scaleb(-places)


def q_qty(value) -> Decimal:
    return to_decimal(value).quantize(quantum(QTY_PLACES), rounding=ROUND_HALF_UP)


def q_money(value) -> Decimal:
    return to_decimal(value).quantize(quantum(MONEY_PLACES), rounding=ROUND_HALF_UP)


def q_price(value) -> Decimal:
    return to_decimal(value).quantize(quantum(PRICE_PLACES), rounding=ROUND_HALF_UP)


def q_unit_cost(value) -> Decimal:
    return to_decimal(value).quantize(quantum(UNIT_COST_PLACES), rounding=ROUND_HALF_UP)
