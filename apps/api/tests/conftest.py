"""Test environment — set BEFORE any app import, for every test module."""

import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_meyraki.db"
os.environ["UPLOAD_DIR"] = "var/test_uploads"
os.environ["MEYRAKI_USE_AGENTS"] = "off"  # tests never call the live API
os.environ["MEYRAKI_RATELIMIT"] = "off"  # rate limits tested explicitly in test_auth


# Start every run from a clean database. This must happen here (before any
# module imports app.db and opens a pooled connection) — deleting the file
# later makes SQLite report 'readonly database' on the stale handles.
Path("test_meyraki.db").unlink(missing_ok=True)
