"""FastAPI-ის დამოკიდებულებები: ბაზის სესია და მიმდინარე მომხმარებელი."""
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.system import User


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request) -> User | None:
    """ავტორიზაცია ჩაირთვება მომდევნო ეტაპზე. მანამდე ცვლილებები ჟურნალში „სისტემის“ სახელით იწერება."""
    return None
