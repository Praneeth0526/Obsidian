@echo off
REM HPE CPP Project - Start script for Windows

IF NOT EXIST .env (
  echo [!] .env file not found. Copy .env.example to .env and fill in your values.
  pause
  exit /b 1
)

IF NOT EXIST .venv (
  echo [!] Virtual environment not found.
  echo Run: python -m venv .venv  then  .venv\Scripts\activate  then  pip install -r requirements.txt
  pause
  exit /b 1
)

IF NOT EXIST logs mkdir logs

echo ================================================
echo   HPE Object Storage Search Engine
echo ================================================
echo.

echo [*] Starting indexer...
start "HPE Indexer" /B .venv\Scripts\python.exe indexer\indexer.py > logs\indexer.log 2>&1

echo [*] Starting API...
start "HPE API" /B .venv\Scripts\python.exe api\api.py > logs\api.log 2>&1

echo.
echo ------------------------------------------------
echo   Search UI  ^>  http://localhost:8000
echo   API Docs   ^>  http://localhost:8000/docs
echo ------------------------------------------------
echo.
echo Logs: logs\indexer.log and logs\api.log
echo Close this window or run stop.bat to stop.
pause
