"""პროგრამის კონფიგურაცია.

ყველა მნიშვნელობა შეიძლება შეიცვალოს გარემოს ცვლადით (environment variable),
მაგალითად სხვა პორტზე გასაშვებად: APP_PORT=8080 python run.py
"""
import os
import sys
from pathlib import Path

# .exe-დან (PyInstaller) გაშვებისას პროგრამის ფაილები დროებით საქაღალდეშია (sys._MEIPASS),
# მონაცემები კი უნდა ინახებოდეს მუდმივ ადგილას — არა იქ, საიდანაც .exe გაუშვეს (მაგ. Downloads),
# რომ .exe-ის გადატანისას ან წაშლისას ბაზა არ დაიკარგოს.
FROZEN = getattr(sys, "frozen", False)
APP_FOLDER_NAME = "WarmoebisAghricxva"

if FROZEN:
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _user_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / APP_FOLDER_NAME
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    _user_root = BASE_DIR
APP_DIR = BASE_DIR / "app"

DATA_DIR = Path(os.environ.get("APP_DATA_DIR", _user_root / "data"))
BACKUP_DIR = Path(os.environ.get("APP_BACKUP_DIR", _user_root / "backups"))
DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = os.environ.get("APP_DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")

# მხოლოდ ლოკალური კომპიუტერიდან ხელმისაწვდომი (უსაფრთხოებისთვის).
HOST = os.environ.get("APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_PORT", "8000"))

# რამდენი სარეზერვო ასლი შევინახოთ
BACKUP_KEEP = int(os.environ.get("APP_BACKUP_KEEP", "30"))
BACKUP_INTERVAL_HOURS = 24

TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
FONTS_DIR = STATIC_DIR / "fonts"
SECRET_KEY_FILE = DATA_DIR / "secret.key"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def get_secret_key() -> str:
    """სესიის (შეტყობინებების) ხელმოწერის გასაღები — იქმნება ერთხელ და ინახება data/-ში."""
    ensure_dirs()
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    import secrets

    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key, encoding="utf-8")
    return key
