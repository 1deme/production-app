"""ენუმების ქართული სახელები (ჟურნალისა და ინტერფეისისთვის)."""
from app.i18n import t


def status_label(status) -> str:
    return t(f"status.{getattr(status, 'value', status)}")


def movement_label(mtype) -> str:
    return t(f"movement.{getattr(mtype, 'value', mtype)}")
