#!/usr/bin/env bash
# One-command run: start CommonSense dashboard (Streamlit) and open browser.
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && source .env && set +a
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}${PWD}/src"
(sleep 2 && open "http://localhost:8501") &
exec python -m streamlit run src/commonsense/dashboard/app.py --server.headless true
