@echo off
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up... this takes a minute.
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Checking packages...
python -m pip install --upgrade pip --quiet --disable-pip-version-check
pip install -r requirements.txt --quiet --disable-pip-version-check
python app.py
pause
