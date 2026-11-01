#!/usr/bin/env bash
# Local start: API on :8020 and the web app on :5180 . Vite dev server with a proxy to the API.
#   ./run.sh            start both
#   ./run.sh test       run the tests
# ./run.sh eval run the evaluation. Takes about an hour on qwen3:8b.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r backend/requirements.txt
fi
case "${1:-start}" in
  test) PYTHONPATH=. .venv/bin/python -m pytest backend/tests -q ;;
  eval) PYTHONPATH=. .venv/bin/python -m backend.evals.run_evals "${@:2}" ;;
  generate) PYTHONPATH=. .venv/bin/python -m backend.app.ehr.generate ;;
  start)
    ollama list >/dev/null 2>&1 || echo "Ollama is not running: start it, then: ollama pull qwen3:8b"
    [ -d frontend/node_modules ] || (cd frontend && npm install --silent)
    .venv/bin/uvicorn backend.app.main:app --port 8020 &
    API=$!
    trap 'kill $API' EXIT
    (cd frontend && npx vite --port 5180)
    ;;
esac
