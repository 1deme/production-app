import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.units import enum_column


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """მომხმარებლები. ავტორიზაცია ჩაირთვება მომდევნო ეტაპზე — ცხრილი უკვე მზადაა."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditLog(Base):
    """ვინ, როდის და რა შეცვალა."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # create / update / delete / ...
    entity: Mapped[str] = mapped_column(String(64), index=True)  # ingredient / receipt / ...
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entity_label: Mapped[str] = mapped_column(String(300), default="")  # მაგ. ინგრედიენტის სახელი
    # JSON: {"ველი": [ძველი, ახალი], ...}
    changes: Mapped[str] = mapped_column(Text, default="{}")


class Setting(Base):
    """პროგრამის პარამეტრები (key → value)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
