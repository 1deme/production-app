"""სადემონსტრაციო მონაცემები ბრძანების ხაზიდან:  python seed.py

იგივეა, რაც პროგრამაში ღილაკი „სადემონსტრაციო მონაცემების ჩატვირთვა“: მუშაობს მხოლოდ ცარიელ ბაზაზე.
"""
import sys

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from app.bootstrap import prepare_database  # noqa: E402
from app.db import get_session_factory  # noqa: E402
from app.services import demo  # noqa: E402
from app.services.errors import BusinessError  # noqa: E402


def main() -> None:
    prepare_database()
    session = get_session_factory()()
    try:
        demo.load_demo(session)
        session.commit()
        print("სადემონსტრაციო მონაცემები ჩაიტვირთა.")
    except BusinessError as e:
        session.rollback()
        print(e.msg.render())
    finally:
        session.close()


if __name__ == "__main__":
    main()
