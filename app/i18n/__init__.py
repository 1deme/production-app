"""ტექსტების თარგმნის მარტივი ფუნქცია: t("ingredients.title") → „ინგრედიენტები“."""
import logging

from app.i18n.ka import MESSAGES

log = logging.getLogger(__name__)


def t(key: str, **params) -> str:
    text = MESSAGES.get(key)
    if text is None:
        # ტექსტი აკლია — ვაჩვენებთ გასაღებს, რომ შეცდომა მაშინვე შესამჩნევი იყოს.
        log.warning("i18n: ტექსტი ვერ მოიძებნა: %s", key)
        return key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            log.warning("i18n: პარამეტრი აკლია ტექსტში %s: %s", key, params)
            return text
    return text


def has(key: str) -> bool:
    return key in MESSAGES
