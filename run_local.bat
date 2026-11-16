@echo off
rem ---------------------------------------------------------------------------
rem  Run the price checker on this computer, for testing.
rem
rem  Double-click this file, or type  run_local  in a terminal in this folder,
rem  then open http://127.0.0.1:8001/  in the browser.  Ctrl+C stops it.
rem
rem  Uses the project's own .venv and config\settings_local.py (DEBUG on,
rem  SQLite, no HTTPS redirect). A plain  python manage.py runserver  would
rem  use the live server's settings and the global Python instead, which is
rem  what fails with "No module named 'decouple'".
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The .venv folder is missing. Create it once with:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Brings the local database up to date after a pull; does nothing otherwise.
".venv\Scripts\python.exe" manage.py migrate --settings=config.settings_local
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8001 --settings=config.settings_local
