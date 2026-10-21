"""SQLite ბაზის სარეზერვო ასლები.

გამოიყენება SQLite-ის ჩაშენებული backup API — ასლი სწორია მაშინაც, როცა პროგრამა მუშაობს.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PREFIX = "app-"
SUFFIX = ".db"


@dataclass
class BackupFile:
    path: Path
    created_at: datetime
    size: int

    @property
    def name(self) -> str:
        return self.path.name


def sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///"):]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def create_backup(db_path: Path, backup_dir: Path, keep: int, tag: str = "") -> Path | None:
    """ასლის შექმნა. თუ ბაზა ჯერ არ არსებობს — None."""
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = f"_{tag}" if tag else ""
    target = backup_dir / f"{PREFIX}{stamp}{suffix}{SUFFIX}"
    n = 1
    while target.exists():
        target = backup_dir / f"{PREFIX}{stamp}{suffix}-{n}{SUFFIX}"
        n += 1
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    prune(backup_dir, keep)
    return target


def list_backups(backup_dir: Path) -> list[BackupFile]:
    if not backup_dir.exists():
        return []
    files = []
    for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}"):
        st = p.stat()
        files.append(BackupFile(path=p, created_at=datetime.fromtimestamp(st.st_mtime), size=st.st_size))
    files.sort(key=lambda f: f.created_at, reverse=True)
    return files


def prune(backup_dir: Path, keep: int) -> None:
    for old in list_backups(backup_dir)[max(keep, 1):]:
        try:
            old.path.unlink()
        except OSError:
            pass


def last_backup_time(backup_dir: Path) -> datetime | None:
    files = list_backups(backup_dir)
    return files[0].created_at if files else None
