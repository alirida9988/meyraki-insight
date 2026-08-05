import os
from pathlib import Path

# Load apps/api/.env (KEY=VALUE lines) without adding a dependency; real env wins.
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://meyraki:meyraki_dev@localhost:5433/meyraki",
)

# Local disk in dev; swapped for S3-compatible storage before pilot.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "var/uploads"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def agents_enabled() -> bool:
    """Real agents when a key is present; MEYRAKI_USE_AGENTS=off forces stubs.
    Read at call time, not import time — import order must never decide this."""
    return os.environ.get("MEYRAKI_USE_AGENTS", "auto") != "off" and bool(ANTHROPIC_API_KEY)


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FLOORPLAN_TYPES = {"image/png", "image/jpeg", "application/pdf"}
FLOORPLAN_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"%PDF-")
