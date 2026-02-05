"""Load configuration from environment (EDGAR_EMAIL, DATA_DIR, EDGAR_LOCAL_DATA_DIR)."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Resolve project root (parent of src) first so we can set cache default.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Parquet output directory (default relative to project root).
_DATA_DIR = os.environ.get("DATA_DIR", "data/parquet").strip()
DATA_DIR = Path(_DATA_DIR) if Path(_DATA_DIR).is_absolute() else (_PROJECT_ROOT / _DATA_DIR)

# edgartools cache: use a directory inside the project so we don't need ~/.edgar (avoids sandbox/permission issues).
# Set before any edgartools import so ticker lookup and cache work when run from the project.
if "EDGAR_LOCAL_DATA_DIR" not in os.environ or not os.environ.get("EDGAR_LOCAL_DATA_DIR", "").strip():
    _edgar_cache = DATA_DIR.parent / ".edgar"
    os.environ["EDGAR_LOCAL_DATA_DIR"] = str(_edgar_cache.resolve())

# SEC EDGAR User-Agent (required by SEC); edgartools uses this via set_identity().
EDGAR_EMAIL = os.environ.get("EDGAR_EMAIL", "").strip()
