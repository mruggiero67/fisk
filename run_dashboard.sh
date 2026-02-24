#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source /Users/michael@jaris.io/venv/basic-pandas/bin/activate
export JIRA_CONFIG="${JIRA_CONFIG:-$HOME/bin/jira_epic_config.yaml}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$(cat "$HOME/bin/.anthropic_key" 2>/dev/null || echo '')}"
exec uvicorn fisk.api.app:app --host 127.0.0.1 --port 8000 --reload
