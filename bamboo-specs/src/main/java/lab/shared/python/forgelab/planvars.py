"""Plan variables: the two a run carries, and the checks that come first.

PROV takes a cluster name and, optionally, the name of the config to build
from. Everything else about a cluster — its type, its sizing, which
technologies it runs — lives in that config file, so there is nothing left for
a plan variable to override and no placeholder machinery to keep in step with
the spec.

Depends on clusterconfig for the loading and the validation; clusterconfig must
not depend on this module back.
"""

from __future__ import annotations

import re

from . import clusterconfig
from .proc import die

CLUSTER_NAME_RE = re.compile(r"[a-z0-9-]+")


def require_cluster_name(cluster: str, usage: str) -> str:
    """The cluster name, or die. Every entrypoint's first check."""
    if not cluster:
        die(usage)
    if not CLUSTER_NAME_RE.fullmatch(cluster):
        die(f"cluster_name must match ^[a-z0-9-]+$ (got '{cluster}')")
    return cluster


def resolve(cluster: str, config_name: str, usage: str):
    """(cluster, ClusterConfig) — the one call an entrypoint makes first.

    An empty cluster_config means "the config named after the cluster", which
    is what a run that only fills in cluster_name should get.
    """
    require_cluster_name(cluster, usage)
    name = config_name.strip() or cluster
    if not CLUSTER_NAME_RE.fullmatch(name):
        die(f"cluster_config must match ^[a-z0-9-]+$ (got '{name}')")
    return cluster, clusterconfig.load(name)
