"""File storage: object storage when configured, local disk otherwise.

Uploaded floorplans and generated reports lived only on the API container's disk. That
survives a restart, because the volume is mounted — and does not survive moving hosts,
rebuilding the machine, or running a second API container, which is exactly what a
deployment eventually does. It also meant the one thing a client pays for, the report,
was the least durable artifact in the system.

Setting MEYRAKI_S3_BUCKET moves both to S3-compatible storage (Cloudflare R2 here).
Leaving it unset keeps local disk, so development and the test suite need no credentials
and no network.

Two asymmetries below are deliberate:

- **Reads fall back to disk; writes do not.** After the switch, every key already in the
  database points at a file on disk — including reports behind signed client links that
  have already been sent. Reading falls back so those keep working. Writing must never
  fall back, because a silent fall back to disk during an S3 outage would scatter new
  reports across two places and lose them on the next deploy, while appearing to succeed.
- **A missing object is an error, not an empty result.** Callers turn a load failure into
  "report no longer available — re-run the analysis", which is honest. Returning empty
  bytes would produce a zero-byte PDF a client cannot open.
"""

import os
import re
import uuid
from pathlib import Path

from . import settings

# Object-storage keys are namespaced so backups and uploads can share one bucket.
PREFIX = "uploads/"

# Keys are minted here as hex plus a suffix. Anything else — a path separator, a parent
# reference — is either a bug or an attempt to read outside the store, and both deserve
# the same refusal rather than a best-effort interpretation.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]+$")

_client = None


def _s3():
    """The S3 client, or None when object storage is not configured."""
    global _client
    bucket = os.environ.get("MEYRAKI_S3_BUCKET", "").strip()
    if not bucket:
        return None
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("MEYRAKI_S3_ENDPOINT") or None,
            aws_access_key_id=os.environ.get("MEYRAKI_S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("MEYRAKI_S3_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("MEYRAKI_S3_REGION", "auto"),
            config=Config(signature_version="s3v4", retries={"max_attempts": 3},
                          connect_timeout=10, read_timeout=60),
        )
    return _client


def _bucket() -> str:
    return os.environ.get("MEYRAKI_S3_BUCKET", "").strip()


def _checked(key: str) -> str:
    if not key or not _SAFE_KEY.match(key):
        raise ValueError("invalid storage key")
    return key


def _local_path(key: str) -> Path:
    path = (settings.UPLOAD_DIR / _checked(key)).resolve()
    # Belt and braces: the key pattern already forbids separators, but the store is read
    # with a path join and this is the boundary where that would matter.
    if not path.is_relative_to(settings.UPLOAD_DIR.resolve()):
        raise ValueError("invalid storage key")
    return path


def save(data: bytes, suffix: str) -> str:
    key = f"{uuid.uuid4().hex}{suffix}"
    client = _s3()
    if client is not None:
        # No fallback on failure: raising loses one upload, falling back loses the file
        # on the next deploy while telling the studio it worked.
        client.put_object(Bucket=_bucket(), Key=PREFIX + key, Body=data)
        return key
    path = settings.UPLOAD_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def load(key: str) -> bytes:
    _checked(key)
    client = _s3()
    if client is not None:
        try:
            return client.get_object(Bucket=_bucket(), Key=PREFIX + key)["Body"].read()
        except Exception:
            # Written before object storage was switched on. Fall through to disk rather
            # than 404 a report whose signed link is already in a client's inbox.
            pass
    path = _local_path(key)
    if not path.exists():
        raise FileNotFoundError(key)
    return path.read_bytes()


def delete(key: str) -> None:
    _checked(key)
    client = _s3()
    if client is not None:
        try:
            client.delete_object(Bucket=_bucket(), Key=PREFIX + key)
        except Exception:
            pass  # deleting what is already gone is not a failure
    # Always clear the local copy too: after the switch a key may exist in both places,
    # and a delete that leaves one behind is not a delete.
    _local_path(key).unlink(missing_ok=True)
