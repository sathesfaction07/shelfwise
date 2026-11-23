param([switch]$CheckOnly)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:AWS_MODE = "floci"
if (-not $env:AWS_ENDPOINT_URL) { $env:AWS_ENDPOINT_URL = "http://localhost:4566" }
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:AWS_EC2_METADATA_DISABLED = "true"
$env:REQUIRE_LLM_SUCCESS = "true"
$env:PYTHONUTF8 = "1"
python scripts/check_floci.py
if ($LASTEXITCODE -ne 0) { throw "Floci connectivity/model checks failed; see docs/FLOCI.md." }
if (-not $CheckOnly) { python app.py }
