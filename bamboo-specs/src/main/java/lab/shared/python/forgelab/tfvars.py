"""Per-cluster sizing files under lab/shared/clusters/."""

from __future__ import annotations

from pathlib import Path

from . import paths
from .proc import die


def resolve(cluster: str) -> Path:
    """lab/shared/clusters/<cluster>.tfvars if present, else defaults.tfvars."""
    candidate = paths.CLUSTERS_DIR / f"{cluster}.tfvars"
    if candidate.is_file():
        return candidate
    fallback = paths.CLUSTERS_DIR / "defaults.tfvars"
    if fallback.is_file():
        return fallback
    die("no tfvars found (need lab/shared/clusters/defaults.tfvars)")


def parse_cluster_type(text: str) -> str:
    """Read `cluster_type = "k8s"` out of a tfvars file's contents.

    Returns "" when the key is absent — the caller reports that as an invalid
    cluster_type, naming the file it came from.
    """
    for line in text.splitlines():
        if line.startswith("cluster_type"):
            parts = line.split('"')
            if len(parts) >= 2:
                return parts[1]
    return ""
