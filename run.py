"""პროგრამის გაშვება ერთი ბრძანებით:  python run.py

1. ქმნის ბაზის სარეზერვო ასლს;
2. ანახლებს ბაზის სტრუქტურას (მიგრაციები);
3. უშვებს სერვერს და ხსნის ბრაუზერს.
"""
import logging
import socket
import sys
import threading
import time
import traceback
import webbrowser

# Windows-ის კონსოლში ქართული ტექსტი სწორად რომ გამოჩნდეს
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:  # noqa: BLE001
        pass
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import uvicorn  # noqa: E402

from app import config  # noqa: E402
from app.bootstrap import prepare_database  # noqa: E402


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = f"http://{config.HOST}:{config.PORT}/"

    if port_in_use(config.HOST, config.PORT):
        print(f"პროგრამა უკვე გაშვებულია (ან პორტი {config.PORT} დაკავებულია). ვხსნი ბრაუზერს: {url}")
        webbrowser.open(url)
        time.sleep(4)  # .exe-ის ფანჯარა მყისიერად რომ არ დაიხუროს — შეტყობინება წაკითხვადი იყოს
        return

    print("ბაზის მომზადება...")
    prepare_database()
    print(f"მონაცემები ინახება: {config.DATA_DIR}")
    print(f"სარეზერვო ასლები: {config.BACKUP_DIR}")

    from app.main import create_app

    app = create_app()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\nპროგრამა გაშვებულია: {url}")
    print("გასაჩერებლად დახურეთ ეს ფანჯარა ან დააჭირეთ Ctrl+C.\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        # .exe-ში ფანჯარა შეცდომისას მყისიერად იხურება — ვაჩერებთ, რომ შეცდომა წაიკითხონ
        traceback.print_exc()
        print("\nპროგრამა ვერ გაეშვა. გადაიღეთ ამ ფანჯრის სურათი და გაუგზავნეთ დეველოპერს.")
        if config.FROZEN:
            input("დახურვისთვის დააჭირეთ Enter-ს...")
        sys.exit(1)
