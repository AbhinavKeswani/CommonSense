#!/usr/bin/env bash
# One-command run: start CommonSense dashboard (Streamlit) and open browser.
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && source .env && set +a
# edgartools cache inside project so we don't need ~/.edgar (avoids sandbox/permission issues)
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}${PWD}/src"
[ -z "${EDGAR_LOCAL_DATA_DIR}" ] && export EDGAR_LOCAL_DATA_DIR="${PWD}/data/.edgar"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ] && [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
elif [ -z "$PYTHON" ]; then
  PYTHON=python3
fi
(sleep 2 && open "http://localhost:8501") &
exec "$PYTHON" -m streamlit run src/commonsense/dashboard/app.py --server.headless true
