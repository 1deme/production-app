#!/usr/bin/env bash
# წარმოების აღრიცხვა — გაშვება Linux/macOS-ზე
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "პირველი გაშვება: ვამზადებ გარემოს (რამდენიმე წუთი)..."
  if ! python3 -m venv .venv; then
    echo "ვირტუალური გარემო ვერ შეიქმნა. Ubuntu/Debian-ზე გაუშვით: sudo apt install python3-venv"
    rm -rf .venv
    exit 1
  fi
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python run.py "$@"
