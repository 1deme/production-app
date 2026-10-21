# PyInstaller-ის კონფიგურაცია: ერთი .exe ფაილი, რომელსაც Python არ სჭირდება.
# აწყობა:  pyinstaller production_app.spec   →   dist/WarmoebisAghricxva.exe
# (Windows-ის .exe იწყობა Windows-ზე — ამას ავტომატურად აკეთებს GitHub Actions, იხ. .github/workflows/build.yml)
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("app")  # alembic/env.py-ს მიგრაციებისთვის სჭირდება მოდელები
    + collect_submodules("uvicorn")
    + ["python_multipart", "multipart"]
)

datas = [
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
    ("alembic", "alembic"),
    ("alembic.ini", "."),
]

a = Analysis(
    ["run.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "uvloop"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WarmoebisAghricxva",
    console=True,  # შავი ფანჯარა: ჩანს მისამართი და შეცდომები; დახურვით პროგრამა ჩერდება
    upx=False,  # UPX-ით შეკუმშული .exe ანტივირუსებს უფრო ხშირად აეჭვებს
    runtime_tmpdir=None,
)
