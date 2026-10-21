"""ბიზნეს-ლოგიკის შეცდომები.

სერვისები ტექსტს პირდაპირ არ წერენ — აბრუნებენ გასაღებს (i18n key) და პარამეტრებს,
ხოლო ინტერფეისი მას ქართულ ტექსტად გარდაქმნის.
"""
from dataclasses import dataclass, field

from app.i18n import t


@dataclass(frozen=True)
class Msg:
    key: str
    params: dict = field(default_factory=dict)

    def render(self) -> str:
        return t(self.key, **self.params)


class BusinessError(Exception):
    """ერთი შეცდომა, რომელიც მომხმარებელს უნდა ვაჩვენოთ."""

    def __init__(self, key: str, **params):
        self.msg = Msg(key, params)
        super().__init__(self.msg.render())


class ValidationError(Exception):
    """ფორმის შეცდომები ველების მიხედვით: {"name": [Msg, ...], ...}"""

    def __init__(self, errors: dict[str, list[Msg]]):
        self.errors = errors
        super().__init__("; ".join(m.render() for msgs in errors.values() for m in msgs))

    def rendered(self) -> dict[str, list[str]]:
        return {f: [m.render() for m in msgs] for f, msgs in self.errors.items()}


class ErrorBag:
    """შეცდომების დაგროვება, რომ ყველა ერთად ვაჩვენოთ და არა სათითაოდ."""

    def __init__(self):
        self.errors: dict[str, list[Msg]] = {}

    def add(self, field_name: str, key: str, **params) -> None:
        self.errors.setdefault(field_name, []).append(Msg(key, params))

    def add_msg(self, field_name: str, msg: Msg) -> None:
        self.errors.setdefault(field_name, []).append(msg)

    def __bool__(self) -> bool:
        return bool(self.errors)

    def raise_if_any(self) -> None:
        if self.errors:
            raise ValidationError(self.errors)
