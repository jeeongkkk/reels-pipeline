Set-Location $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $env:LOCALAPPDATA "ms-playwright"
Write-Host "Starting Authority Reels Studio..."
Write-Host "Open http://localhost:8501"
python -m streamlit run app.py
