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

# <repo>/bamboo-specs — Maven's project root. `mvn exec:java` publishes from here.
SPECS_ROOT = REPO_ROOT / "bamboo-specs"
# .../java/lab — one directory per plan. publish_specs.py discovers specs under it.
LAB_DIR = SHARED_DIR.parent

TF_DIR = SHARED_DIR / "terraform"
CLUSTERS_DIR = SHARED_DIR / "clusters"
ANSIBLE_DIR = SHARED_DIR / "ansible"
ANSIBLE_CFG = ANSIBLE_DIR / "ansible.cfg"
SITE_YML = ANSIBLE_DIR / "site.yml"
INV_DIR = ANSIBLE_DIR / "inventory"

FORGELAB_HOME = Path.home() / ".forgelab"
SSH_KEY = FORGELAB_HOME / "id_ed25519"
# Bamboo personal access token, used only to publish plans. Same home, same
# 0600 posture as the cluster credentials files.
BAMBOO_PAT = FORGELAB_HOME / "bamboo_pat"
SSH_CONF_DIR = FORGELAB_HOME / "ssh_config.d"
# One line: where the cluster registry lives on this host. See registry_dir().
REGISTRY_POINTER = FORGELAB_HOME / "registry_dir"


def registry_dir() -> Path:
    """Where the per-cluster info files live, in order of precedence.

    REPO_ROOT is only right outside CI. Bamboo gives every plan its own checkout
    (xml-data/build-dir/FORGE-PROV-JOB1, ...-DEPROV-JOB1), so a repo-relative
    registry lands in an ephemeral directory nobody looks at, and deprovision
    deletes from a different one than provision wrote to.

    1. $FORGELAB_REGISTRY_DIR — an explicit override for one run.
    2. ~/.forgelab/registry_dir — the host's durable answer, seeded by
       run_agent.py. A file rather than the agent's environment because the
       agent is long-lived: a registry that only works when someone remembered
       how to launch the JVM is a trap.
    3. This checkout, which is what a hand-run `make provision` should use.
    """
    override = os.environ.get("FORGELAB_REGISTRY_DIR")
    if override:
        return Path(override)
    if REGISTRY_POINTER.is_file():
        pointed = REGISTRY_POINTER.read_text().strip()
        if pointed:
            return Path(pointed)
    return REPO_ROOT / "cluster_registered"


# Resolved once per process, which is all an entrypoint ever is.
REGISTRY_DIR = registry_dir()


def ansible_env(env: dict) -> dict:
    """Return `env` with ANSIBLE_CONFIG pointing at the shared ansible.cfg.

    ansible.cfg is only auto-loaded from the CWD, and these scripts run from the
    caller's directory, so point ansible at it explicitly. Without this,
    interpreter_python=auto_silent and friends are silently ignored and every
    play re-emits the interpreter-discovery warning.
    """
    return {**env, "ANSIBLE_CONFIG": str(ANSIBLE_CFG)}
