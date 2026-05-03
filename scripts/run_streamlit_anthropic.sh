#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

export RSE_EXTRACTION_PROVIDER="${RSE_EXTRACTION_PROVIDER:-anthropic}"
export RSE_EXTRACTION_MODEL="${RSE_EXTRACTION_MODEL:-claude-sonnet-4-6}"
export RSE_ENABLE_SECOND_PASS_VERIFICATION="${RSE_ENABLE_SECOND_PASS_VERIFICATION:-true}"
export RSE_ENABLE_ARBITRATION="${RSE_ENABLE_ARBITRATION:-false}"

: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY in your shell or .env.local before running the app.}"

.venv/bin/python scripts/manage_db.py
.venv/bin/streamlit run streamlit_app.py
