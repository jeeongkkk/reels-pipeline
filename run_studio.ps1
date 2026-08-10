Set-Location $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $env:LOCALAPPDATA "ms-playwright"
Write-Host "Starting Authority Reels Studio..."
Write-Host "Open http://localhost:8501"
# Prefer Python 3.10 (moviepy / full local stack)
$py310 = & py -3.10 -c "import sys; print(sys.executable)" 2>$null
if ($py310) {
    & $py310 -m streamlit run app.py
} else {
    python -m streamlit run app.py
}
