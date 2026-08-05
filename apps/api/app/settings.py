import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://meyraki:meyraki_dev@localhost:5433/meyraki",
)

# Local disk in dev; swapped for S3-compatible storage before pilot.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "var/uploads"))

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FLOORPLAN_TYPES = {"image/png", "image/jpeg", "application/pdf"}
