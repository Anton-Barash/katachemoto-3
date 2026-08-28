@echo off
cd /d "%~dp0"
rem Запускаем Streamlit в новом окне через PowerShell Start-Process
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0.venv\Scripts\python.exe' -ArgumentList '-m','streamlit','run','%~dp0app.py' -WindowStyle Normal"
timeout /t 5 >nul
start "" "http://localhost:8501"
exit /b
