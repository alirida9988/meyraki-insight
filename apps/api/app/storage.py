"""File storage. Local disk now; the key-based interface maps 1:1 onto S3 later."""

import uuid
from pathlib import Path

from . import settings


def save(data: bytes, suffix: str) -> str:
    key = f"{uuid.uuid4().hex}{suffix}"
    path = settings.UPLOAD_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def load(key: str) -> bytes:
    path = (settings.UPLOAD_DIR / key).resolve()
    if not path.is_relative_to(settings.UPLOAD_DIR.resolve()):
        raise ValueError("invalid storage key")
    return path.read_bytes()
