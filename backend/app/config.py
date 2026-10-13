"""Runtime configuration (contract §2).

The root `.env` is loaded once on import with `override=False`, so real environment
variables (Docker `--env-file`, test monkeypatching) always win. Settings are read
lazily through functions so tests can change the environment between app instances.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> parents[2] is the repo root, parents[1] is backend/.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]

load_dotenv(REPO_ROOT / ".env", override=False)

SNAPSHOT_INTERVAL = 30.0  # seconds between background portfolio snapshots
HISTORY_WINDOW_HOURS = 24
HISTORY_MAX_POINTS = 500


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def db_path() -> Path:
    raw = os.environ.get("DB_PATH", "").strip()
    return Path(raw) if raw else REPO_ROOT / "db" / "finally.db"


def static_dir() -> Path:
    raw = os.environ.get("STATIC_DIR", "").strip()
    return Path(raw) if raw else BACKEND_DIR / "static"


def openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def llm_mock() -> bool:
    return _env_bool("LLM_MOCK")


def enable_reset() -> bool:
    return _env_bool("ENABLE_RESET")
