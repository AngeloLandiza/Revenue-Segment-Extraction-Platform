#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

export FITCH_EXTRACTION_PROVIDER="${FITCH_EXTRACTION_PROVIDER:-anthropic}"
export FITCH_EXTRACTION_MODEL="${FITCH_EXTRACTION_MODEL:-claude-sonnet-4-6}"
export FITCH_ENABLE_SECOND_PASS_VERIFICATION="${FITCH_ENABLE_SECOND_PASS_VERIFICATION:-true}"
export FITCH_ENABLE_ARBITRATION="${FITCH_ENABLE_ARBITRATION:-false}"

: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY in your shell or .env.local before running the app.}"

.venv/bin/python scripts/manage_db.py
.venv/bin/streamlit run streamlit_app.py
