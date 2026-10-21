"""ტექსტის ნორმალიზაცია ძიებისა და უნიკალურობის შესამოწმებლად.

- ზედმეტი ჰარები იშლება;
- ლათინური ასოები პატარავდება;
- ქართული მთავრული (ᲐᲑᲒ…) გარდაიქმნება მხედრულად (აბგ…) — Python-ის casefold() ამას აკეთებს;
- Unicode NFC ფორმა, რომ ვიზუალურად ერთნაირი სიმბოლოები ერთნაირადაც შედარდეს.
"""
import re
import unicodedata

_WS = re.compile(r"\s+")


def clean_name(value: str | None) -> str:
    """სახელი შესანახად: რეგისტრი შენარჩუნებულია, ჰარები გასწორებულია."""
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value)
    return _WS.sub(" ", value).strip()


def normalize_text(value: str | None) -> str:
    """სახელი შედარებისთვის/ძიებისთვის."""
    return clean_name(value).casefold()


# შტრიხკოდის სკანერი კლავიატურასავით „ბეჭდავს“. თუ კომპიუტერზე ჩართულია ქართული განლაგება,
# ლათინური ასოები ქართულად შემოვა (მაგ. „AB12“ → „აბ12“). ციფრები არ იცვლება.
# ეს ცხრილი აბრუნებს ქართული სტანდარტული განლაგების ასოებს შესაბამის ლათინურ კლავიშებზე.
_GEORGIAN_LAYOUT_TO_LATIN = {
    "ა": "a", "ბ": "b", "გ": "g", "დ": "d", "ე": "e", "ვ": "v", "ზ": "z", "თ": "t",
    "ი": "i", "კ": "k", "ლ": "l", "მ": "m", "ნ": "n", "ო": "o", "პ": "p", "ჟ": "j",
    "რ": "r", "ს": "s", "ტ": "t", "უ": "u", "ფ": "f", "ქ": "q", "ღ": "r", "ყ": "y",
    "შ": "s", "ჩ": "c", "ც": "c", "ძ": "z", "წ": "w", "ჭ": "w", "ხ": "x", "ჯ": "j",
    "ჰ": "h",
}
_LAYOUT_TABLE = str.maketrans(_GEORGIAN_LAYOUT_TO_LATIN)


def clean_barcode(value: str | None) -> str:
    """შტრიხკოდი შესანახად: ყველა ჰარი და უხილავი სიმბოლო იშლება."""
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value)
    return "".join(ch for ch in value if not ch.isspace() and unicodedata.category(ch)[0] != "C")


def barcode_key(value: str | None) -> str:
    """შტრიხკოდი შედარებისთვის: განლაგებისა და რეგისტრისგან დამოუკიდებლად."""
    return clean_barcode(value).casefold().translate(_LAYOUT_TABLE)


def looks_like_barcode(value: str | None) -> bool:
    """უხეში შეფასება: ძიების ტექსტი შტრიხკოდს ჰგავს თუ დასახელებას?"""
    v = clean_name(value)
    if not v or " " in v or len(v) < 4:
        return False
    digits = sum(ch.isdigit() for ch in v)
    return digits / len(v) >= 0.6


def escape_like(value: str) -> str:
    """LIKE-ის სპეციალური სიმბოლოების (% _) ეკრანირება."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
