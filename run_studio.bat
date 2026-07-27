@echo off
cd /d "%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\ms-playwright"
echo Starting Authority Reels Studio...
echo Browser will open at http://localhost:8501
python -m streamlit run app.py
pause
