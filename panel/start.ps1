$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    python -m venv .venv
    & $runtime -m pip install -r requirements.txt
}
& $runtime -m uvicorn app:app --host 127.0.0.1 --port 8765
