"""Test environment — set BEFORE any app import, for every test module."""

import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_meyraki.db"
os.environ["UPLOAD_DIR"] = "var/test_uploads"
os.environ["MEYRAKI_USE_AGENTS"] = "off"  # tests never call the live API
os.environ["MEYRAKI_RATELIMIT"] = "off"  # rate limits tested explicitly in test_auth
# Open signup for the suite, whatever the deployment allows. Nearly every test registers
# a throwaway account, so a real allowlist in apps/api/.env silently turned 25 of them
# into 403s — the deployment's configuration leaking into the tests, which is what these
# assignments exist to prevent. test_registration_allowlist sets it explicitly per test.
os.environ["MEYRAKI_ALLOWED_EMAILS"] = ""


# Start every run from a clean database. This must happen here (before any
# module imports app.db and opens a pooled connection) — deleting the file
# later makes SQLite report 'readonly database' on the stale handles.
Path("test_meyraki.db").unlink(missing_ok=True)
