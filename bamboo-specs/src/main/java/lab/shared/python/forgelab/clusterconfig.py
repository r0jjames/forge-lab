"""Per-cluster configuration files under cluster_configs/.

Replaces the flat `<cluster>.tfvars` files. One YAML file states a cluster's
type, its node sizing, and which technologies it runs — and everything
downstream (the Terraform nodes map, the Ansible inventory groups, the registry
sizing) is derived from it, so those can no longer disagree.

The host agent has no venv and this package is standard library only, so the
parser is hand-written. It accepts a deliberately small subset and rejects the
rest by name rather than tolerating half of YAML: a parser that quietly handles
sequences but not anchors teaches the reader that the whole language works here.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from . import paths
from .proc import die

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# A cluster_nodes key becomes the VM's name verbatim, and multipass rejects an
# instance name that is not alphanumeric-with-dashes. Validate it here, where
# the error can name the file, rather than at `terraform apply` ten minutes in.
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _fail(source, lineno, message):
    die(f"{source}:{lineno}: {message}")


def _strip_comment(raw: str) -> str:
    """Drop a trailing comment. A '#' only opens one at line start or after
    whitespace — a bare '#' inside a value is part of the value, as in real
    YAML, so a value is never silently truncated."""
    for index, char in enumerate(raw):
        if char == "#" and (index == 0 or raw[index - 1] in " \t"):
            return raw[:index].rstrip()
    return raw.rstrip()


def parse(text: str, source: str) -> dict:
    """Nested plain dicts from the accepted subset. Every scalar is a str.

    Accepted: two-space block mappings, `key: value`, `key:` openers, `#`
    comments (whole-line and trailing), and blank lines. Values may be wrapped
    in matching single or double quotes.
    """
    root: dict = {}
    # The sentinel indent is -2 so a top-level key at indent 0 reads as one
    # level in from it, the same relationship every other pair has.
    stack = [(-2, root)]

    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            _fail(source, lineno, "tabs are not allowed; indent with two spaces")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            _fail(source, lineno, "sequences are not supported")
        if stripped.startswith("---") or stripped.startswith("..."):
            _fail(source, lineno, "document markers are not supported")

        content = _strip_comment(raw)
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            _fail(source, lineno, f"indent of {indent} is not a multiple of two")
        if ":" not in content:
            _fail(source, lineno, f"expected 'key: value' or 'key:', got {stripped!r}")

        key, _, value = content.partition(":")
        key, value = key.strip(), value.strip()
        if not _KEY_RE.match(key):
            _fail(source, lineno, f"invalid key {key!r}")
        if value[:1] in ("{", "["):
            _fail(source, lineno, "flow collections are not supported")
        if value[:1] in ("&", "*"):
            _fail(source, lineno, "anchors and aliases are not supported")
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        while indent <= stack[-1][0]:
            stack.pop()
        if indent > stack[-1][0] + 2:
            _fail(source, lineno, f"indent of {indent} jumps more than one level")

        parent = stack[-1][1]
        if key in parent:
            _fail(source, lineno, f"duplicate key {key!r}")
        if value:
            parent[key] = value
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))

    return root


CLUSTER_TYPES = ("k8s", "dcos")

# k9s is deliberately absent: it is a kubectl TUI, installed unconditionally by
# the k8s role, not something a cluster opts into.
TECHNOLOGIES = ("keycloak", "hdfs", "opensearch", "splunk")

# Keycloak runs as pods on the Kubernetes cluster the cluster_nodes form, so it
# is the one technology that owns no VM role.
NODELESS_TECHNOLOGIES = ("keycloak",)

# The node roles each technology owns. Unconstrained keys here would build VMs
# that no ansible role installs onto, so this is the same unknown-key rule the
# rest of the config already applies — a node role costs an ansible role too,
# so the config was never the only place a new one has to be declared.
TECHNOLOGY_NODES = {
    "keycloak": (),
    "hdfs": ("namenode", "datanode"),
    "opensearch": ("master",),
    "splunk": ("cluster-manager", "indexer", "search-head"),
}

# Splunk's roles are not interchangeable and their counts are fixed by the
# product, not by taste: one cluster manager per cluster, one search head (a
# search head *cluster* needs three members and a deployer, which this lab does
# not build), and at least two indexers because the manager is configured with
# replication_factor = 2 and a peer cannot replicate to itself.
SPLUNK_EXACT_COUNTS = (("cluster-manager", 1), ("search-head", 1))
SPLUNK_MIN_INDEXERS = 2

# The k8s and dcos roles both take their control node from groups['management'][0],
# and the Keycloak play targets management[0].
CONTROL_ROLE = "management"

NODE_KEYS = ("count", "cpu", "memory", "disk")
_SIZE_RE = re.compile(r"^\d+[MG]$")
_BOOLS = {"true": True, "false": False}


class NodeSpec(NamedTuple):
    role: str
    group: str
    count: int
    cpu: int
    memory: str
    disk: str


def _at(source, message):
    die(f"{source}: {message}")


def _mapping(value, source, dotted):
    if not isinstance(value, dict):
        _at(source, f"'{dotted}' must be a block of keys, not a value")
    return value


def _require(parent, key, source, dotted):
    if key not in parent:
        _at(source, f"missing required key '{dotted}'")
    return parent[key]


def _reject_unknown(parent, allowed, source, prefix):
    for key in parent:
        if key not in allowed:
            dotted = f"{prefix}.{key}" if prefix else key
            _at(source, f"unknown key '{dotted}'; known: {' '.join(allowed)}")


def _int(parent, key, source, dotted, minimum):
    raw = _require(parent, key, source, dotted)
    if not isinstance(raw, str) or not raw.isdigit():
        _at(source, f"'{dotted}' must be a whole number (got {raw!r})")
    number = int(raw)
    if number < minimum:
        _at(source, f"'{dotted}' {key} must be at least {minimum} (got {number})")
    return number


def _size(parent, key, source, dotted):
    raw = _require(parent, key, source, dotted)
    if not isinstance(raw, str) or not _SIZE_RE.match(raw):
        _at(source, f"'{dotted}' must look like 4G or 512M (got {raw!r})")
    return raw


def _bool(parent, key, source, dotted):
    raw = _require(parent, key, source, dotted)
    if raw not in _BOOLS:
        _at(source, f"'{dotted}' must be true or false (got {raw!r})")
    return _BOOLS[raw]


def _node_spec(block, role, source, dotted):
    if not _ROLE_RE.match(role):
        _at(source, f"role {role!r} must match ^[a-z][a-z0-9-]*$ — it becomes "
                    f"the VM name {{cluster}}-{role}-<n>")
    _mapping(block, source, dotted)
    _reject_unknown(block, NODE_KEYS, source, dotted)
    return NodeSpec(
        role=role,
        group=role.replace("-", "_"),
        count=_int(block, "count", source, f"{dotted}.count", 0),
        cpu=_int(block, "cpu", source, f"{dotted}.cpu", 1),
        memory=_size(block, "memory", source, f"{dotted}.memory"),
        disk=_size(block, "disk", source, f"{dotted}.disk"),
    )


class ClusterConfig:
    """A validated cluster config, and every view of it the pipeline needs."""

    def __init__(self, source, cluster_type, cluster_roles, tech_roles, enabled):
        self.source = source
        self.cluster_type = cluster_type
        self._cluster_roles = cluster_roles
        self._tech_roles = tech_roles
        self._enabled = enabled

    def enabled(self) -> list:
        """Enabled technology names, in file order. Ansible still gets these as
        the comma-separated `addons` extra-var, so no `when:` clause changes."""
        return list(self._enabled)

    def roles(self) -> list:
        """Every NodeSpec that will be built: cluster nodes, then each enabled
        technology's nodes in file order."""
        specs = list(self._cluster_roles)
        for name in self._enabled:
            specs += self._tech_roles.get(name, [])
        return specs

    def nodes_map(self, cluster: str) -> dict:
        """The `nodes` variable Terraform applies: one entry per VM."""
        nodes = {}
        for spec in self.roles():
            for index in range(spec.count):
                nodes[f"{cluster}-{spec.role}-{index + 1}"] = {
                    "cpus": spec.cpu,
                    "memory": spec.memory,
                    "disk": spec.disk,
                }
        return nodes

    def children(self) -> dict:
        """The inventory's `:children` groups, in the order they are emitted.

        k8s_nodes is every cluster-node group — those are the VMs that receive
        kubelet, and a technology node never does. Each enabled technology that
        owns VMs also gets a <name>_nodes group, which is what site.yml targets.
        """
        groups = {"k8s_nodes": [s.group for s in self._cluster_roles]}
        for name in self._enabled:
            specs = self._tech_roles.get(name)
            if specs:
                groups[f"{name}_nodes"] = [s.group for s in specs]
        return groups

    def sizing_by_role(self) -> dict:
        """{role: {cpu, mem, disk}} for the cluster registry."""
        return {
            spec.role: {
                "cpu": str(spec.cpu), "mem": spec.memory, "disk": spec.disk,
            }
            for spec in self.roles()
        }


def from_text(text: str, source: str) -> ClusterConfig:
    """Parse and validate. Every error names the file and the dotted key."""
    data = parse(text, source)
    _reject_unknown(data, ("cluster", "cluster_nodes", "technologies"), source, "")

    cluster = _mapping(_require(data, "cluster", source, "cluster.type"), source, "cluster")
    _reject_unknown(cluster, ("type",), source, "cluster")
    cluster_type = _require(cluster, "type", source, "cluster.type")
    if cluster_type not in CLUSTER_TYPES:
        _at(source, f"cluster.type must be one of [{' '.join(CLUSTER_TYPES)}] "
                    f"(got {cluster_type!r})")

    nodes = _mapping(
        _require(data, "cluster_nodes", source, "cluster_nodes"), source, "cluster_nodes"
    )
    if CONTROL_ROLE not in nodes:
        _at(source, f"cluster_nodes must define '{CONTROL_ROLE}' — the k8s and "
                    f"dcos roles take their control node from it")
    cluster_roles = [
        _node_spec(block, role, source, f"cluster_nodes.{role}")
        for role, block in nodes.items()
    ]
    control = next(s for s in cluster_roles if s.role == CONTROL_ROLE)
    if control.count < 1:
        _at(source, f"cluster_nodes.{CONTROL_ROLE}.count must be at least 1")

    technologies = _mapping(data.get("technologies", {}), source, "technologies")
    enabled, tech_roles = [], {}
    for name, block in technologies.items():
        dotted = f"technologies.{name}"
        if name not in TECHNOLOGIES:
            _at(source, f"unknown technology {name!r}; "
                        f"known: {' '.join(TECHNOLOGIES)}")
        _mapping(block, source, dotted)
        _reject_unknown(block, ("enabled", "nodes"), source, dotted)
        if not _bool(block, "enabled", source, f"{dotted}.enabled"):
            # A disabled technology keeps its sizing in the file, unvalidated,
            # so switching it back on does not mean retyping it.
            continue
        enabled.append(name)
        if name in NODELESS_TECHNOLOGIES:
            if "nodes" in block:
                _at(source, f"{name} runs on the cluster and declares no nodes")
            continue
        if "nodes" not in block:
            _at(source, f"{dotted} must declare nodes when enabled")
        node_blocks = _mapping(block["nodes"], source, f"{dotted}.nodes")
        _reject_unknown(node_blocks, TECHNOLOGY_NODES[name], source, f"{dotted}.nodes")
        if not node_blocks:
            _at(source, f"{dotted} must declare nodes when enabled")
        tech_roles[name] = [
            _node_spec(spec, f"{name}-{node}", source, f"{dotted}.nodes.{node}")
            for node, spec in node_blocks.items()
        ]
        if sum(spec.count for spec in tech_roles[name]) < 1:
            _at(source, f"{dotted} is enabled but every node count is 0 — "
                        f"set enabled: false to park it instead")

    # VM names are <cluster>-<role>-<n> and provision.py finds a role's VMs by
    # that name prefix, so one role name being a prefix of another makes their
    # VM sets overlap and puts the same host in two ansible groups. Reject the
    # config rather than the ambiguity: a cluster_nodes role named `hdfs` would
    # otherwise swallow every hdfs-namenode and hdfs-datanode VM.
    built = [spec.role for spec in cluster_roles]
    for name in enabled:
        built += [spec.role for spec in tech_roles.get(name, [])]

    # A cluster_nodes key and a technology's node can name the exact same role
    # (e.g. cluster_nodes.hdfs-namenode alongside technologies.hdfs.nodes.namenode).
    # The prefix guard below deliberately skips self-comparison (other != role),
    # which also skips this case, so catch an exact duplicate first.
    seen = set()
    for role in built:
        if role in seen:
            _at(source, f"role {role!r} is declared twice — a cluster_nodes key "
                        f"and a technology's node cannot name the same role")
        seen.add(role)

    for role in built:
        for other in built:
            if other != role and other.startswith(f"{role}-"):
                _at(source, f"role {other!r} starts with role {role!r}, so their "
                            f"VM names collide — rename one")

    if "hdfs" in enabled:
        namenodes = [s for s in tech_roles["hdfs"] if s.role == "hdfs-namenode"]
        if len(namenodes) != 1 or namenodes[0].count != 1:
            _at(source, "hdfs has exactly one NameNode: "
                        "technologies.hdfs.nodes.namenode.count must be 1")

    if "splunk" in enabled:
        counts = {spec.role: spec.count for spec in tech_roles["splunk"]}
        for node, expected in SPLUNK_EXACT_COUNTS:
            if counts.get(f"splunk-{node}") != expected:
                _at(source, f"splunk has exactly one {node}: "
                            f"technologies.splunk.nodes.{node}.count must be "
                            f"{expected}")
        if counts.get("splunk-indexer", 0) < SPLUNK_MIN_INDEXERS:
            _at(source, f"technologies.splunk.nodes.indexer.count must be at "
                        f"least {SPLUNK_MIN_INDEXERS} — the cluster manager runs "
                        f"replication_factor = 2 and a peer cannot replicate to "
                        f"itself")

    return ClusterConfig(source, cluster_type, cluster_roles, tech_roles, enabled)


def path_for(name: str):
    return paths.CLUSTER_CONFIGS_DIR / f"{name}_cluster.yaml"


def load(name: str) -> ClusterConfig:
    """Read, parse and validate `cluster_configs/<name>_cluster.yaml`.

    There is no fallback. The config is selected by name, so a typo must fail
    naming what it looked for rather than silently building a default cluster.
    """
    path = path_for(name)
    if not path.is_file():
        available = sorted(
            p.name[: -len("_cluster.yaml")]
            for p in paths.CLUSTER_CONFIGS_DIR.glob("*_cluster.yaml")
        ) if paths.CLUSTER_CONFIGS_DIR.is_dir() else []
        die(f"no config at {path} (available: {', '.join(available) or 'none'})")
    return from_text(path.read_text(), str(path))
