"""What code produced this analysis.

Every report carries numbers a client may query months later — a flow score, a capacity
estimate, a list of moves. The audit trail already records each step's output, its cost
and its model id, which is enough to see *what* the pipeline decided. It was not enough to
see *which build* decided it, and the things that move those numbers most are the agent
prompts, which live in ordinary source files.

So a report written before a prompt change was indistinguishable from one written after.
`CONTRACT_VERSION` does not close that: it tracks the shape of the contracts, not the
prompts, and it barely moves.

The commit sha does. It is baked into the image at build time, because a container has no
git history to consult:

    docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) ...

Outside a container — a developer's machine, the test suite — it falls back to asking git
directly, so the stamp is useful in development without anyone having to remember a flag.
Failing both, it reports "unknown" rather than raising: not knowing the build is a gap in
an audit trail, never a reason to refuse an analysis.
"""

import os
import subprocess
from functools import lru_cache
from pathlib import Path

UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def build_version() -> str:
    """The commit this process was built from. Cached: it cannot change while running."""
    baked = (os.environ.get("MEYRAKI_GIT_SHA") or "").strip()
    if baked:
        return baked[:40]

    # Not in an image. Ask git, but never let a missing binary, a non-repository or a slow
    # filesystem take down an API process — this is provenance, not function.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    sha = result.stdout.strip()
    return sha[:40] if result.returncode == 0 and sha else UNKNOWN
