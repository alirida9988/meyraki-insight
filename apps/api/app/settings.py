import os
from pathlib import Path

def load_env_file(path: Path) -> None:
    """Minimal dotenv: KEY=VALUE lines, `export ` prefix and single/double quotes
    handled, comments skipped. Real environment always wins (setdefault)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_env_file(Path(__file__).resolve().parent.parent / ".env")

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
