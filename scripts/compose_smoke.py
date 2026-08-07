"""Smoke-test a running Meyraki stack — the deployment's own proof.

    docker compose up --build -d
    apps/api/.venv/bin/python scripts/compose_smoke.py

Answers the question a Dockerfile cannot: does the thing actually serve a client report
from inside its containers? It drives the public HTTP surface only — no imports from
`app`, no database access — so it works identically against compose, staging, or
production.

Costs model credits: it runs one real analysis. Point it elsewhere with MEYRAKI_SMOKE_API
and MEYRAKI_SMOKE_PLAN.
"""

import os
import sys
import time

import httpx

API = os.environ.get("MEYRAKI_SMOKE_API", "http://localhost:8000")
PLAN = os.environ.get(
    "MEYRAKI_SMOKE_PLAN",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "apps", "api", "tests", "golden", "cleo_hotel.png"),
)
DEADLINE_S = 600

results: list[tuple[bool, str, str]] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    results.append((bool(ok), what, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def main() -> int:
    run = str(int(time.time()))
    print(f"smoke-testing {API}")

    # 1. The stack is up and the API answers before anything else is attempted.
    for attempt in range(60):
        try:
            r = httpx.get(f"{API}/health", timeout=10)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        check(False, "API answers /health", f"no response after 120s at {API}")
        return 1
    check(True, "API answers /health")

    client = httpx.Client(base_url=API, timeout=120, follow_redirects=True)

    # 2. A studio can be created and is logged in by the session cookie.
    r = client.post("/auth/register", json={
        "email": f"smoke-{run}@meyraki.dev",
        "password": "smoke-password-1",
        "org_name": f"Smoke {run}",
    })
    if not check(r.status_code in (200, 201), "a studio can register", f"HTTP {r.status_code}"):
        return 1

    r = client.post("/projects", json={"name": f"Smoke {run}", "space_type": "hotel"})
    if not check(r.status_code in (200, 201), "a project can be created", f"HTTP {r.status_code}"):
        return 1
    project_id = r.json()["id"]

    # 3. A real plan survives the upload path (magic-byte sniffing, size limits, storage
    #    on whatever volume the deployment mounted).
    with open(PLAN, "rb") as handle:
        plan_bytes = handle.read()
    r = client.post(f"/projects/{project_id}/uploads", params={"kind": "floorplan"},
                    files={"file": ("plan.png", plan_bytes, "image/png")})
    if not check(r.status_code in (200, 201), "a floorplan uploads and persists",
                 f"HTTP {r.status_code} for {len(plan_bytes)//1024}KB"):
        return 1
    upload_id = r.json()["id"]

    # 4. The whole pipeline runs inside the container, agents and all.
    r = client.post(f"/projects/{project_id}/analyses",
                    json={"floorplan_upload_id": upload_id, "objectives": ["guest_flow"]})
    if not check(r.status_code in (200, 201), "an analysis starts", f"HTTP {r.status_code}"):
        return 1
    analysis_id = r.json()["id"]

    started = time.monotonic()
    detail: dict = {}
    while time.monotonic() - started < DEADLINE_S:
        detail = client.get(f"/analyses/{analysis_id}").json()
        if detail.get("status") in ("done", "failed", "rejected"):
            break
        time.sleep(3)
    elapsed = time.monotonic() - started
    failed = next((s for s in detail.get("steps", []) if s.get("status") == "failed"), None)
    if not check(detail.get("status") == "done", "the pipeline completes",
                 f"{detail.get('status')} in {elapsed:.0f}s"
                 + (f" — {failed['name']}: {(failed.get('error') or '')[:160]}" if failed else "")):
        return 1

    # 5. The deliverable itself. A PDF that is not a PDF is the failure that matters.
    r = client.get(f"/analyses/{analysis_id}/report.pdf")
    check(r.status_code == 200 and r.content.startswith(b"%PDF-"),
          "the branded PDF renders (Chromium is present in the image)",
          f"HTTP {r.status_code}, {len(r.content)//1024}KB, magic={r.content[:5]!r}")

    # 6. The cost guard reports what the run spent.
    cost = detail.get("cost") or {}
    check(isinstance(cost.get("usd"), (int, float)) and cost.get("by_step"),
          "the analysis reports its model spend",
          f"${cost.get('usd')} over {len(cost.get('by_step') or [])} calls")

    # 7. Org isolation, from outside. A second studio must not see the first one's work.
    other = httpx.Client(base_url=API, timeout=60, follow_redirects=True)
    other.post("/auth/register", json={
        "email": f"rival-{run}@meyraki.dev",
        "password": "smoke-password-1",
        "org_name": f"Rival {run}",
    })
    r = other.get(f"/analyses/{analysis_id}")
    check(r.status_code == 404, "another studio gets 404, not 403 or data",
          f"HTTP {r.status_code}")

    print("\n" + "=" * 70)
    failures = [r for r in results if not r[0]]
    print(f"{len(results)} checks · {len(failures)} FAILED")
    for _ok, what, why in failures:
        print(f"  FAIL: {what} — {why}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
