#!/usr/bin/env python3
"""Republish the Bamboo plans when main is pushed from this clone.

Installed by `make hooks-install` (git config core.hooksPath infra/githooks).
Runs the plan's publisher as a subprocess rather than importing it: infra/
imports forgelab and nothing else under lab/.

This hook never blocks a push. Being unable to push while the lab is switched
off is a worse failure than a stale plan, and the FORGE-SPECS plan reconciles
from the real main on its next poll either way.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH = (
    REPO_ROOT
    / "bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py"
)
MAIN_REF = "refs/heads/main"

# Caps the publish subprocess so "port-forward up, Bamboo not serving yet" —
# the reachability probe is only a TCP connect — cannot hang the push open.
PUBLISH_TIMEOUT = 300


def touches_main(stdin_text: str) -> bool:
    """True when this push updates refs/heads/main.

    git feeds pre-push one line per ref on stdin:
        <local ref> <local sha> <remote ref> <remote sha>
    A deletion sends an all-zero local sha — nothing to publish from that.
    """
    for line in stdin_text.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        _, local_sha, remote_ref, _ = fields
        if remote_ref == MAIN_REF and set(local_sha) != {"0"}:
            return True
    return False


def main(argv, stdin_text) -> int:
    try:
        if not touches_main(stdin_text):
            return 0
        subprocess.run(
            [sys.executable, str(PUBLISH), "--skip-if-unreachable"],
            check=True,
            timeout=PUBLISH_TIMEOUT,
        )
    except BaseException as err:  # a push must survive anything here
        print(f"warning: specs publish failed, pushing anyway: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], sys.stdin.read()))
    except BaseException:
        sys.exit(0)
