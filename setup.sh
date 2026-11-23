#!/usr/bin/env bash
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}==>${NC} $1"; }
ok()    { echo -e "${GREEN} ok ${NC} $1"; }
warn()  { echo -e "${YELLOW}warn${NC} $1"; }
fail()  { echo -e "${RED}fail${NC} $1"; exit 1; }

info "Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install it first:"
    echo "  macOS:         brew install python@3.12"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    echo "  RHEL/Fedora:   sudo dnf install python3"
    fail "python3 missing"
fi

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$PY_OK" != "1" ]; then
    echo "Python 3.11 or newer is required. Found $PY_VER."
    echo "  macOS:         brew install python@3.12"
    echo "  Ubuntu/Debian: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv"
    fail "python $PY_VER too old"
fi
ok "Python $PY_VER"

info "Creating virtual environment"
if [ ! -d .venv ]; then
    python3 -m venv .venv || fail "could not create .venv (on Debian/Ubuntu you may need: sudo apt install python3-venv)"
fi
ok ".venv ready"

info "Installing dependencies [dev]"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]" || fail "pip install failed"
ok "dependencies installed"

info "Verifying imports"
.venv/bin/python - <<'PYCHECK' || fail "core imports failed"
import boto3, pandas, selenium, flask  # noqa: F401
import product_intelligence.agents.discovery  # noqa: F401
import product_intelligence.finops.budget  # noqa: F401
PYCHECK
ok "imports resolve"

info "Checking for Chrome/ChromeDriver (required for Selenium)"
if command -v google-chrome >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
    ok "Chrome found"
else
    warn "Chrome not found. The live Amazon provider will not work."
    warn "  Install: macOS: brew install --cask google-chrome"
    warn "           Ubuntu: sudo apt install chromium-browser"
fi

if command -v chromedriver >/dev/null 2>&1; then
    ok "ChromeDriver found"
else
    warn "ChromeDriver not found. The live Amazon provider will not work."
    warn "  Install: macOS: brew install --cask chromedriver"
    warn "           Ubuntu: sudo apt install chromium-driver"
fi

info "Running the test suite"
.venv/bin/python -m pytest -q || warn "some tests failed (may need Chrome/ChromeDriver or AWS credentials)"
ok "tests complete"

info "Checking AWS credentials (optional - tests use mock provider)"
if .venv/bin/python -c "import boto3; boto3.client('sts').get_caller_identity()" >/dev/null 2>&1; then
    ok "AWS credentials resolve"
else
    warn "No AWS credentials found (tests work without them)."
    warn "  Configure with: aws configure"
    warn "  Or set AWS_PROFILE environment variable"
fi

cat <<'DONE'

Setup complete.

  source .venv/bin/activate

  pytest                    # run all tests (uses mock provider)
  python app.py             # start Flask web UI on http://localhost:5050

Tests use a mock provider and don't require Chrome or AWS credentials.
The live Amazon provider requires Chrome + ChromeDriver + network access.
Real LLM calls require AWS credentials and Bedrock model access (Claude Haiku 4.5 + Nova Micro).
DONE
