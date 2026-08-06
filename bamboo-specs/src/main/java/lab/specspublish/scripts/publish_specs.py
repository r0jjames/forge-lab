#!/usr/bin/env python3
"""Publish every Bamboo Specs plan in this repo to the Bamboo server.

The single publish path. `make specs-publish`, the pre-push hook and the
FORGE-SPECS plan all run this file, so the three cannot drift.

There is no list of plans anywhere: the layout is the list. One directory per
plan under lab/, one <Name>Spec.java inside it, so the classes are discoverable
from the filesystem and adding a plan needs no bookkeeping elsewhere.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import paths, proc  # noqa: E402

# Mirrors SpecConstants.BAMBOO_URL — the Java specs publish to it and this
# script probes it. PublishSpecsSpecTest pins the two together.
BAMBOO_URL = "http://localhost:8085"

USAGE = "usage: publish_specs.py [--skip-if-unreachable]"

# The token env var doubles as the one-off override, so a run needs no file.
TOKEN_ENV = "FORGELAB_BAMBOO_PAT"


def spec_classes(lab_dir: Path) -> list:
    """Every plan spec under lab/, as fully qualified class names.

    lab/provisioncluster/ProvisionClusterSpec.java is
    lab.provisioncluster.ProvisionClusterSpec: the directory is the package
    leaf, the file stem is the class. Sorted, so the publish order is stable.
    """
    return sorted(
        f"lab.{spec.parent.name}.{spec.stem}" for spec in lab_dir.glob("*/*Spec.java")
    )


def resolve_token(env: dict, pat_file: Path) -> str:
    """The Bamboo PAT: environment first, then the host file."""
    from_env = env.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    if pat_file.is_file():
        from_file = pat_file.read_text().strip()
        if from_file:
            return from_file
    proc.die(
        f"no Bamboo token: set {TOKEN_ENV} or put the PAT in {pat_file} "
        "(chmod 600). Bamboo: Profile > Personal access tokens."
    )


def render_credentials(token: str) -> str:
    """The .credentials file the Bamboo Specs Maven plugin reads. Pure."""
    return f"token={token}\n"


def write_credentials(token: str, dest: Path) -> Path:
    """Write .credentials owner-readable only. Returns its path.

    Opened with the mode already restricted rather than write_text() + chmod():
    the latter leaves a world-readable window on a file holding a live token.
    Also fchmod on the open fd to ensure 0600 holds when overwriting an existing
    file at a looser mode.
    """
    handle = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(handle, 0o600)
    with os.fdopen(handle, "w") as out:
        out.write(render_credentials(token))
    return dest
