"""Test environment — set BEFORE any app import, for every test module."""

import os

os.environ["DATABASE_URL"] = "sqlite:///./test_meyraki.db"
os.environ["UPLOAD_DIR"] = "var/test_uploads"
os.environ["MEYRAKI_USE_AGENTS"] = "off"  # tests never call the live API
os.environ["MEYRAKI_RATELIMIT"] = "off"  # rate limits tested explicitly in test_auth

