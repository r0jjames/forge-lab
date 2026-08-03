"""Every path the lab uses, derived from this file's own location.

Nothing here reads the current working directory: Bamboo runs these scripts
from its build directory and you run them from your shell, so self-location is
the only stable anchor.
"""

from __future__ import annotations

import os
from pathlib import Path

# .../lab/shared/python/forgelab/paths.py -> .../lab/shared
SHARED_DIR = Path(__file__).resolve().parents[2]
# .../<repo>/bamboo-specs/src/main/java/lab/shared -> <repo>
REPO_ROOT = SHARED_DIR.parents[5]

TF_DIR = SHARED_DIR / "terraform"
CLUSTERS_DIR = SHARED_DIR / "clusters"
ANSIBLE_DIR = SHARED_DIR / "ansible"
ANSIBLE_CFG = ANSIBLE_DIR / "ansible.cfg"
SITE_YML = ANSIBLE_DIR / "site.yml"
INV_DIR = ANSIBLE_DIR / "inventory"

# Tracked, unlike the generated inventory: one file per live cluster, written by
# provision and removed by deprovision.
#
# REPO_ROOT is only the right answer outside CI. Bamboo gives every plan its own
# checkout (xml-data/build-dir/FORGE-PROV-JOB1, ...-DEPROV-JOB1), so a
# repo-relative registry would land in an ephemeral directory nobody looks at,
# and deprovision would try to delete from a different one than provision wrote.
# FORGELAB_REGISTRY_DIR points both plans at one durable clone; run_agent.py sets
# it to the clone the agent was started from.
REGISTRY_DIR = Path(
    os.environ.get("FORGELAB_REGISTRY_DIR", REPO_ROOT / "cluster_registered")
)

FORGELAB_HOME = Path.home() / ".forgelab"
SSH_KEY = FORGELAB_HOME / "id_ed25519"
SSH_CONF_DIR = FORGELAB_HOME / "ssh_config.d"


def ansible_env(env: dict) -> dict:
    """Return `env` with ANSIBLE_CONFIG pointing at the shared ansible.cfg.

    ansible.cfg is only auto-loaded from the CWD, and these scripts run from the
    caller's directory, so point ansible at it explicitly. Without this,
    interpreter_python=auto_silent and friends are silently ignored and every
    play re-emits the interpreter-discovery warning.
    """
    return {**env, "ANSIBLE_CONFIG": str(ANSIBLE_CFG)}
