@echo off
rem Warmoebis aghricxva - gashveba Windows-ze
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo პირველი გაშვება: ვამზადებ გარემოს ^(რამდენიმე წუთი^)...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python ვერ მოიძებნა. დააინსტალირეთ Python 3.11+ საიტიდან python.org ^(მონიშნეთ "Add Python to PATH"^).
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ბიბლიოთეკების ინსტალაცია ვერ მოხერხდა. შეამოწმეთ ინტერნეტ-კავშირი და სცადეთ თავიდან.
    rmdir /s /q .venv
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
