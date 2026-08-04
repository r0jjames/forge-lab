"""Per-cluster sizing files under lab/shared/clusters/."""

from __future__ import annotations

import re
from pathlib import Path

from . import paths
from .proc import die

_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$')

CLUSTER_TYPES = ("k8s", "dcos")

# k9s is deliberately absent: it is a kubectl TUI, installed unconditionally by
# the k8s role, not something a cluster opts into.
ADDONS = ("keycloak", "hdfs", "opensearch")


def resolve(cluster: str) -> Path:
    """lab/shared/clusters/<cluster>.tfvars if present, else defaults.tfvars."""
    candidate = paths.CLUSTERS_DIR / f"{cluster}.tfvars"
    if candidate.is_file():
        return candidate
    fallback = paths.CLUSTERS_DIR / "defaults.tfvars"
    if fallback.is_file():
        return fallback
    die("no tfvars found (need lab/shared/clusters/defaults.tfvars)")


def _assignments(text: str):
    """(key, raw value) for every `key = value` line, comments stripped."""
    for line in text.splitlines():
        match = _ASSIGN_RE.match(line.split("#", 1)[0])
        if match:
            yield match.group(1), match.group(2).strip()


def parse(text: str) -> dict:
    """key -> value for every `key = value` line, with strings unquoted.

    These files only ever hold flat scalars (sizes, counts, cluster_type), so a
    line parser is enough; nothing here handles lists, maps or heredocs.
    """
    return {key: raw.strip('"') for key, raw in _assignments(text)}


def parse_cluster_type(text: str) -> str:
    """Read `cluster_type = "k8s"` out of a tfvars file's contents.

    Returns "" when the key is absent or unquoted — terraform would reject the
    unquoted form as well, and the caller already reports an empty cluster_type
    as invalid, naming the file it came from.
    """
    for key, raw in _assignments(text):
        if key == "cluster_type":
            return raw[1:-1] if raw.startswith('"') and raw.endswith('"') else ""
    return ""


def parse_addons(text: str) -> list:
    """The `addons = "a,b"` list, or [] when the key is absent or empty.

    A comma string rather than an HCL list: `parse` only handles flat scalars,
    and this keeps the cluster's settings in one file without teaching it more.
    """
    raw = parse(text).get("addons", "")
    return [name for name in (part.strip() for part in raw.split(",")) if name]


def resolve_addons(override: str, tfvars_text: str, source: str) -> list:
    """The cluster's addon list. The plan variable wins over the tfvars file."""
    if override.strip():
        names = [n for n in (p.strip() for p in override.split(",")) if n]
        source = "the ADDONS override"
    else:
        names = parse_addons(tfvars_text)
    unknown = sorted({n for n in names if n not in ADDONS})
    if unknown:
        die(
            f"unknown addon(s) [{' '.join(unknown)}] from {source}; "
            f"known: {' '.join(ADDONS)}"
        )
    return names
