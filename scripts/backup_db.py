"""Back the database up to object storage, and prove the backup restores.

    scripts/backup_db.py            # dump, upload, prune
    scripts/backup_db.py --verify   # also restore the new dump and check it
    scripts/backup_db.py --verify-only

The deployment runs Postgres in a container with a volume. That is a real database, but
until now nothing copied it anywhere, so a lost disk was a lost business. This is the gap
`docs/05-BUILD-PLAN.md` recorded when the audit-trail claim was corrected.

The part that matters is `--verify`. A backup nobody has restored is a rumour, and the
failure mode is silent: dumps accumulate for months, look healthy in a bucket listing, and
turn out to be empty or truncated on the one day anyone needs them. So verification does
not inspect the file — it restores the dump into a throwaway Postgres container and
compares row counts against the source. A container rather than a scratch database on the
live server, because a managed provider may not grant CREATE DATABASE, and a restore test
that only works in development is a test that will not run in production.

pg_dump itself runs in a `postgres:16-alpine` container too, so no client tooling has to
be installed on the host and the dump utility always matches the server version — a
pg_dump older than its server refuses outright, which is a bad thing to discover during
an incident.

Credentials come from the environment (`apps/api/.env` in development):
    DATABASE_URL, MEYRAKI_S3_ENDPOINT, MEYRAKI_S3_BUCKET,
    MEYRAKI_S3_ACCESS_KEY_ID, MEYRAKI_S3_SECRET_ACCESS_KEY

Exit codes: 0 ok · 1 the backup or its verification failed.
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

PG_IMAGE = "postgres:16-alpine"
PREFIX = "backups/"
KEEP = int(os.environ.get("MEYRAKI_BACKUP_KEEP", "14"))
# Tables whose row counts are compared after a restore. Enough to prove the data came
# back, cheap enough to run every night.
CHECKED_TABLES = ["orgs", "users", "projects", "analyses", "step_runs", "cost_entries"]


def _env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"{name} is not set — source apps/api/.env before running this")
    return value


def _database_url() -> str:
    """The database the application actually uses.

    Taken from `app.settings` rather than required as an environment variable, because a
    backup tool pointed at a different database than the app is worse than no backup: it
    succeeds, uploads, verifies, and protects nothing.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "api"))
    from app import settings  # noqa: E402

    return _plain(settings.DATABASE_URL)


def _plain(dsn: str) -> str:
    """Strip SQLAlchemy's driver suffix.

    The app speaks `postgresql+psycopg://`, which is a SQLAlchemy dialect rather than a
    libpq URI — pg_dump and psycopg.connect both reject it, and the error names the
    scheme rather than the cause.
    """
    return re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)


def _load_dotenv() -> None:
    """Read apps/api/.env without a dependency, and never overwrite a real env var."""
    path = pathlib.Path(__file__).resolve().parent.parent / "apps" / "api" / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _safe(dsn: str) -> str:
    """A DSN with the password removed, for logs."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", dsn)


def _s3():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=_env("MEYRAKI_S3_ENDPOINT"),
        aws_access_key_id=_env("MEYRAKI_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("MEYRAKI_S3_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("MEYRAKI_S3_REGION", "auto"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _docker_dsn(dsn: str) -> str:
    """Rewrite a host-local DSN so it resolves from inside a container.

    `localhost` in a DSN means the host running this script, which inside a container
    means the container itself — the dump would fail with 'connection refused' and look
    like a credentials problem.
    """
    return re.sub(r"@(localhost|127\.0\.0\.1)(:|/)", r"@host.docker.internal\2", dsn)


def dump(dsn: str, path: pathlib.Path) -> None:
    """pg_dump in custom format: compressed, and restorable table by table."""
    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", "--add-host", "host.docker.internal:host-gateway",
         "-e", f"PGCONNECT_TIMEOUT=15", PG_IMAGE,
         "pg_dump", "--format=custom", "--no-owner", "--no-acl", _docker_dsn(dsn)],
        capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        sys.exit(f"pg_dump failed: {proc.stderr.decode()[:400]}")
    path.write_bytes(proc.stdout)
    if path.stat().st_size < 1024:
        sys.exit(f"pg_dump produced {path.stat().st_size} bytes — refusing to upload it")


def source_counts(dsn: str) -> dict[str, int]:
    import psycopg

    counts = {}
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        for table in CHECKED_TABLES:
            try:
                counts[table] = conn.execute(f'select count(*) from "{table}"').fetchone()[0]
            except Exception:
                conn.rollback()  # table absent on this deployment; not an error
    return counts


def verify(dump_path: pathlib.Path, expected: dict[str, int]) -> bool:
    """Restore into a throwaway Postgres and compare row counts against the source."""
    name = f"meyraki-verify-{int(time.time())}"
    password = "verify-only-throwaway"
    print("  starting a throwaway Postgres to restore into…")
    up = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=verify", PG_IMAGE],
        capture_output=True, timeout=300,
    )
    if up.returncode != 0:
        print(f"  could not start the verifier: {up.stderr.decode()[:200]}")
        return False
    try:
        for _ in range(60):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres",
                                    "-d", "verify"], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            print("  the verifier never became ready")
            return False

        restore = subprocess.run(
            ["docker", "exec", "-i", name, "pg_restore", "--no-owner", "--no-acl",
             "-U", "postgres", "-d", "verify"],
            input=dump_path.read_bytes(), capture_output=True, timeout=1800,
        )
        # pg_restore warns about absent roles even on a clean restore; only a missing
        # table would matter, and the counts below are what actually decide.
        if restore.returncode != 0:
            print(f"  pg_restore reported: {restore.stderr.decode()[:200]}")

        ok = True
        for table, want in expected.items():
            got = subprocess.run(
                ["docker", "exec", name, "psql", "-U", "postgres", "-d", "verify", "-tAc",
                 f'select count(*) from "{table}"'],
                capture_output=True, timeout=120,
            )
            actual = got.stdout.decode().strip() or "ERROR"
            mark = "ok " if actual == str(want) else "BAD"
            print(f"  {mark} {table:<14} source {want:>7}   restored {actual:>7}")
            if actual != str(want):
                ok = False
        return ok
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="restore the new dump into a throwaway Postgres and check it")
    parser.add_argument("--verify-only", action="store_true",
                        help="verify the newest backup already in the bucket")
    args = parser.parse_args()

    _load_dotenv()
    dsn = _database_url()
    bucket = _env("MEYRAKI_S3_BUCKET")
    s3 = _s3()
    print(f"database: {_safe(dsn)}")

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "meyraki.dump"

        if args.verify_only:
            listing = s3.list_objects_v2(Bucket=bucket, Prefix=PREFIX).get("Contents", [])
            if not listing:
                print("no backups in the bucket to verify")
                return 1
            newest = max(listing, key=lambda o: o["LastModified"])
            print(f"verifying {newest['Key']} ({newest['Size'] / 1e6:.2f} MB)")
            path.write_bytes(s3.get_object(Bucket=bucket, Key=newest["Key"])["Body"].read())
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
            key = f"{PREFIX}meyraki-{stamp}.dump"
            print("dumping…")
            dump(dsn, path)
            size = path.stat().st_size
            print(f"  {size / 1e6:.2f} MB")
            s3.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
            print(f"uploaded s3://{bucket}/{key}")

            listing = sorted(s3.list_objects_v2(Bucket=bucket, Prefix=PREFIX).get("Contents", []),
                             key=lambda o: o["LastModified"], reverse=True)
            for old in listing[KEEP:]:
                s3.delete_object(Bucket=bucket, Key=old["Key"])
                print(f"pruned {old['Key']}")

        if args.verify or args.verify_only:
            print("verifying the dump restores…")
            if not verify(path, source_counts(dsn)):
                print("VERIFICATION FAILED — this backup would not have saved you")
                return 1
            print("verified: every checked table came back with the same row count")

    return 0


if __name__ == "__main__":
    sys.exit(main())
