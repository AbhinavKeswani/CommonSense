"""Load configuration from environment (EDGAR_EMAIL, DATA_DIR)."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# SEC EDGAR User-Agent (required by SEC); edgartools uses this via set_identity().
EDGAR_EMAIL = os.environ.get("EDGAR_EMAIL", "").strip()

# Parquet output directory (default relative to project root).
_DATA_DIR = os.environ.get("DATA_DIR", "data/parquet").strip()
# Resolve to absolute path relative to project root (parent of src).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(_DATA_DIR) if Path(_DATA_DIR).is_absolute() else (_PROJECT_ROOT / _DATA_DIR)
