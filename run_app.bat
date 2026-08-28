@echo off
cd /d "%~dp0"
rem Запускаем веб-сервер (Flask) в новом окне
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0.venv\Scripts\python.exe' -ArgumentList '%~dp0app_web.py' -WindowStyle Normal"
timeout /t 5 >nul
start "" "http://localhost:5000"
exit /b
