"""Uploads and reports move to object storage without stranding what came before.

Floorplans and generated reports lived only on the API container's disk. That survives a
restart and does not survive moving hosts, rebuilding the machine, or running a second API
container — so the artifact a client actually pays for was the least durable thing in the
system.

The switch is a one-line configuration change, which is precisely why the risky part is
not the S3 code. It is the keys already in the database: every existing analysis points at
a file on disk, including reports behind signed links already sitting in clients' inboxes.
`test_a_key_written_before_the_switch_still_loads` is the guarantee that flipping the
switch does not 404 all of them.

The other asymmetry pinned here: reads fall back to disk, writes never do. A write that
quietly fell back during an S3 outage would report success to the studio and lose the file
on the next deploy — the failure would surface days later as a missing report, with
nothing in the logs tying it to the outage.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest

from app import storage


class FakeS3:
    """Enough of the S3 client to exercise the branch, with a switch for outages."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.broken = False
        self.puts = 0

    def put_object(self, Bucket, Key, Body, **kw):  # noqa: N803 — boto3's signature
        if self.broken:
            raise RuntimeError("S3 unavailable")
        self.puts += 1
        self.objects[Key] = Body

    def get_object(self, Bucket, Key, **kw):  # noqa: N803
        if self.broken:
            raise RuntimeError("S3 unavailable")
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": type("B", (), {"read": lambda _s: self.objects[Key]})()}

    def delete_object(self, Bucket, Key, **kw):  # noqa: N803
        self.objects.pop(Key, None)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setenv("MEYRAKI_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(storage, "_client", fake)
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    return fake


def test_without_a_bucket_nothing_changes(monkeypatch):
    """Development and the test suite must need no credentials and no network."""
    monkeypatch.delenv("MEYRAKI_S3_BUCKET", raising=False)
    monkeypatch.setattr(storage, "_client", None)
    key = storage.save(b"local bytes", ".png")
    assert storage.load(key) == b"local bytes"
    assert (storage.settings.UPLOAD_DIR / key).exists()
    storage.delete(key)


def test_a_configured_bucket_receives_the_object(s3):
    key = storage.save(b"%PDF-1.7 report", ".pdf")
    assert s3.puts == 1
    assert s3.objects[storage.PREFIX + key] == b"%PDF-1.7 report"
    assert not (storage.settings.UPLOAD_DIR / key).exists(), "also written to disk"
    assert storage.load(key) == b"%PDF-1.7 report"


def test_a_key_written_before_the_switch_still_loads(s3, monkeypatch):
    """THE migration guarantee.

    A report delivered last week lives on disk and its signed link is already in a
    client's inbox. Turning object storage on must not break it.
    """
    monkeypatch.delenv("MEYRAKI_S3_BUCKET", raising=False)
    monkeypatch.setattr(storage, "_s3", lambda: None)
    legacy = storage.save(b"%PDF- delivered before the switch", ".pdf")

    monkeypatch.setenv("MEYRAKI_S3_BUCKET", "test-bucket")   # the switch is flipped
    monkeypatch.setattr(storage, "_s3", lambda: s3)
    assert storage.load(legacy) == b"%PDF- delivered before the switch"
    storage.delete(legacy)


def test_a_write_never_falls_back_to_disk(s3):
    """A silent fallback would report success and lose the file on the next deploy."""
    s3.broken = True
    with pytest.raises(Exception):
        storage.save(b"must not be silently written to disk", ".png")
    stray = list(storage.settings.UPLOAD_DIR.glob("*")) if storage.settings.UPLOAD_DIR.exists() else []
    assert not any(p.read_bytes() == b"must not be silently written to disk"
                   for p in stray if p.is_file())


def test_a_missing_object_raises_rather_than_returning_nothing(s3):
    """Callers turn this into 'report no longer available'. Empty bytes would hand a
    client a zero-byte PDF that will not open."""
    with pytest.raises(FileNotFoundError):
        storage.load("deadbeefdeadbeefdeadbeefdeadbeef.pdf")


def test_delete_clears_both_places(s3, monkeypatch):
    """After the switch a key can exist in both, and a delete leaving one behind is not
    a delete — it is a file the studio believes is gone."""
    monkeypatch.setattr(storage, "_s3", lambda: None)
    key = storage.save(b"in both places", ".png")
    monkeypatch.setattr(storage, "_s3", lambda: s3)
    s3.objects[storage.PREFIX + key] = b"in both places"

    storage.delete(key)
    assert storage.PREFIX + key not in s3.objects
    assert not (storage.settings.UPLOAD_DIR / key).exists()


@pytest.mark.parametrize("key", ["../secret.pdf", "a/b.pdf", "", "..", "x\x00.pdf", "/etc/passwd"])
def test_a_hostile_key_is_refused_on_both_backends(s3, key):
    """Keys reach here from the database and from a URL path, so they are not trusted."""
    with pytest.raises(ValueError):
        storage.load(key)
    with pytest.raises(ValueError):
        storage.delete(key)
