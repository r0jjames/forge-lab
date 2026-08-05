"""Plan variables: placeholders, the `none` keyword, and the checks that run
before anything expensive.

Bamboo models a plan variable as a key and a value — there is no description
field — so the default value has to double as the documentation of what the
variable accepts. These placeholder strings are those defaults, and every one
of them is deliberately *not* a legal value: a run that leaves the field
untouched means "no override", and falls back to the cluster's tfvars.

Depends on tfvars for the legal sets and the parsing; tfvars must not depend
on this module back.
"""

from __future__ import annotations

import re

from . import tfvars
from .proc import die

CLUSTER_NAME_RE = re.compile(r"[a-z0-9-]+")

# Defaults of the Bamboo plan variables, mirrored in ProvisionClusterSpec.java
# and pinned there by a test. Both read as menus, so neither can collide with
# a value someone actually meant.
PLACEHOLDER_TYPE = "k8s | dcos"
PLACEHOLDER_ADDONS = "hdfs,keycloak,opensearch (or none)"

# The only way to say "install nothing" without editing a tfvars file. An
# empty value already means "use the tfvars list", so it cannot also mean this.
NONE = "none"


def is_unset(value: str, placeholder: str) -> bool:
    """True when a plan variable carries no override.

    Empty, or the placeholder left exactly as the plan ships it. The match is
    exact on purpose: a sentinel that matches loosely eventually swallows a
    value someone meant, and the near-misses all fail validation with the legal
    set spelled out instead.
    """
    return not value.strip() or value.strip() == placeholder


def split_list(value: str) -> list:
    """A comma string to names: trimmed, empties dropped, order preserved."""
    seen, names = set(), []
    for name in (part.strip() for part in value.split(",")):
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def require_cluster_name(cluster: str, usage: str) -> str:
    """The cluster name, or die. Every entrypoint's first check."""
    if not cluster:
        die(usage)
    if not CLUSTER_NAME_RE.fullmatch(cluster):
        die(f"cluster_name must match ^[a-z0-9-]+$ (got '{cluster}')")
    return cluster


def resolve_cluster_type(override: str, tfvars_text: str, source: str) -> str:
    """The cluster's type. The plan variable wins over the tfvars file."""
    if not is_unset(override, PLACEHOLDER_TYPE):
        cluster_type, source = override.strip(), "the TYPE override"
    else:
        cluster_type = tfvars.parse_cluster_type(tfvars_text)
    if cluster_type not in tfvars.CLUSTER_TYPES:
        hint = (
            " — that is the menu, not a value; pick one"
            if "|" in cluster_type
            else ""
        )
        die(
            f"cluster_type must be one of [{' '.join(tfvars.CLUSTER_TYPES)}] "
            f"(got '{cluster_type}' from {source}){hint}"
        )
    return cluster_type


def resolve_addons(override: str, tfvars_text: str, source: str) -> list:
    """The cluster's addon list. The plan variable wins over the tfvars file.

    Empty or placeholder means the tfvars list; `none` means no addons at all.
    """
    if not is_unset(override, PLACEHOLDER_ADDONS):
        names = split_list(override)
        source = "the ADDONS override"
    else:
        names = tfvars.parse_addons(tfvars_text)

    if NONE in names:
        if len(names) > 1:
            others = " ".join(n for n in names if n != NONE)
            die(
                f"'{NONE}' cannot be combined with [{others}] from {source}; "
                f"use '{NONE}' alone to install no addons"
            )
        return []

    unknown = sorted({n for n in names if n not in tfvars.ADDONS})
    if unknown:
        die(
            f"unknown addon(s) [{' '.join(unknown)}] from {source}; "
            f"known: {' '.join(tfvars.ADDONS)} {NONE}"
        )
    return names


def resolve(cluster: str, type_override: str, addons_override: str, usage: str):
    """Every plan variable at once: (cluster, cluster_type, addons, tfvars path).

    The one call an entrypoint makes before touching a tool or a VM.
    """
    require_cluster_name(cluster, usage)
    path = tfvars.resolve(cluster)
    text = path.read_text()
    cluster_type = resolve_cluster_type(type_override, text, str(path))
    addons = resolve_addons(addons_override, text, str(path))
    return cluster, cluster_type, addons, path
