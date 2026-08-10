@echo off
cd /d "%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\ms-playwright"
echo Starting Authority Reels Studio...
echo Browser will open at http://localhost:8501
REM Prefer Python 3.10 (full deps including moviepy)
py -3.10 -c "import sys" >nul 2>&1
if %errorlevel%==0 (
  py -3.10 -m streamlit run app.py
) else (
  python -m streamlit run app.py
)
pause
