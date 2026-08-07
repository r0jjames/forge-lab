# YAML Cluster Configs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-cluster `.tfvars` files with structured YAML configs in `cluster_configs/`, so a cluster's type, node sizing, and enabled technologies are stated once in one readable file.

**Architecture:** A new stdlib-only module `forgelab/clusterconfig.py` parses a strict YAML subset and validates it, then expands it into everything downstream needs: a single `nodes` map for Terraform, group definitions for the Ansible inventory, and sizing for the cluster registry. `forgelab/tfvars.py` is deleted; Terraform's twenty per-role variables collapse to one map.

**Tech Stack:** Python 3 standard library only, Terraform (multipass provider), Ansible, Bamboo Java Specs, pytest, JUnit 4.

Spec: `docs/superpowers/specs/2026-08-06-cluster-config-yaml-design.md`

## Global Constraints

- **Standard library only.** The host agent has no venv. `forgelab` imports nothing outside the stdlib. No PyYAML anywhere in `lab/`.
- **Commits use Roj's git identity only.** No `Co-Authored-By`, no `Claude-Session`, no "Generated with Claude" footers.
- **Multipass units** are `"4G"` / `"20G"`, never `Gi`.
- **`terraform apply` keeps `-parallelism=1`** — do not touch `terraform.apply_retry`.
- **Fatal errors** go through `forgelab.proc.die`, which raises `LabError`. Never `sys.exit` or bare `raise` in library code.
- **Entrypoints** wrap `main(argv)` in `proc.main`; parsing and rendering stay pure, shelling out goes through `proc.run`.
- **Run the suite** with `pytest bamboo-specs/src/test/python` from the repo root.
- **The control-plane role is `management`**, renamed from `mgmt`. Every group reference moves with it.
- **Ansible group names use underscores**, VM names and roles use dashes: role `hdfs-namenode` → group `hdfs_namenode` → VM `lab1-hdfs-namenode-1`.

---

### Task 1: The config parser

**Files:**
- Create: `bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py`
- Test: `bamboo-specs/src/test/python/test_clusterconfig.py`

**Interfaces:**
- Consumes: `forgelab.proc.die`, `forgelab.paths`
- Produces: `clusterconfig.parse(text, source) -> dict` — nested plain dicts, every scalar a `str`. Raises `LabError` with `<source>:<line>: <message>` on anything outside the subset.

- [ ] **Step 1: Write the failing tests**

Create `bamboo-specs/src/test/python/test_clusterconfig.py`:

```python
"""The strict YAML subset: what it accepts, and what it refuses by name."""

import pytest

from forgelab import clusterconfig
from forgelab.proc import LabError


def test_parses_nested_mappings():
    text = (
        "cluster:\n"
        "  type: k8s\n"
        "cluster_nodes:\n"
        "  management:\n"
        "    count: 1\n"
        "    memory: 4G\n"
    )
    assert clusterconfig.parse(text, "c.yaml") == {
        "cluster": {"type": "k8s"},
        "cluster_nodes": {"management": {"count": "1", "memory": "4G"}},
    }


def test_ignores_comments_and_blank_lines():
    text = "# sizing\n\ncluster:\n  type: k8s  # the default\n"
    assert clusterconfig.parse(text, "c.yaml") == {"cluster": {"type": "k8s"}}


def test_dedents_back_to_an_outer_mapping():
    text = "a:\n  b:\n    c: 1\nd: 2\n"
    assert clusterconfig.parse(text, "c.yaml") == {"a": {"b": {"c": "1"}}, "d": "2"}


def test_strips_matching_quotes_from_a_value():
    assert clusterconfig.parse('a: "4G"\n', "c.yaml") == {"a": "4G"}
    assert clusterconfig.parse("a: '4G'\n", "c.yaml") == {"a": "4G"}


@pytest.mark.parametrize(
    "text,message",
    [
        ("a:\n\tb: 1\n", "tabs are not allowed"),
        ("a:\n  - one\n", "sequences are not supported"),
        ("a: {b: 1}\n", "flow collections are not supported"),
        ("a: [1, 2]\n", "flow collections are not supported"),
        ("a: &anchor\n", "anchors and aliases are not supported"),
        ("a: *anchor\n", "anchors and aliases are not supported"),
        ("---\na: 1\n", "document markers are not supported"),
        ("a:\n   b: 1\n", "not a multiple of two"),
        ("a:\n    b: 1\n", "jumps more than one level"),
        ("just_words\n", "expected 'key: value'"),
        ("a: 1\na: 2\n", "duplicate key 'a'"),
    ],
)
def test_rejects_everything_outside_the_subset(text, message):
    with pytest.raises(LabError, match=message):
        clusterconfig.parse(text, "c.yaml")


def test_errors_name_the_file_and_the_line():
    with pytest.raises(LabError, match=r"c\.yaml:3:"):
        clusterconfig.parse("a:\n  b: 1\n  - two\n", "c.yaml")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_clusterconfig.py -v`
Expected: FAIL — `ImportError: cannot import name 'clusterconfig' from 'forgelab'`

- [ ] **Step 3: Write the parser**

Create `bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py`:

```python
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

from . import paths
from .proc import die

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_clusterconfig.py -v`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py \
        bamboo-specs/src/test/python/test_clusterconfig.py
git commit -m "feat: add the strict YAML subset parser for cluster configs"
```

---

### Task 2: Validation and the ClusterConfig object

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py`
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/paths.py:32` (replace `CLUSTERS_DIR`)
- Test: `bamboo-specs/src/test/python/test_clusterconfig.py`

**Interfaces:**
- Consumes: `clusterconfig.parse` from Task 1.
- Produces:
  - `paths.CLUSTER_CONFIGS_DIR` — `REPO_ROOT / "cluster_configs"`
  - `clusterconfig.CLUSTER_TYPES` = `("k8s", "dcos")`, `clusterconfig.TECHNOLOGIES` = `("keycloak", "hdfs", "opensearch")`, `clusterconfig.CONTROL_ROLE` = `"management"`
  - `clusterconfig.path_for(name) -> Path` — `cluster_configs/<name>_cluster.yaml`
  - `clusterconfig.load(name) -> ClusterConfig`
  - `clusterconfig.from_text(text, source) -> ClusterConfig`
  - `NodeSpec(role, group, count, cpu, memory, disk)` — a `NamedTuple`; `count` and `cpu` are `int`, the rest `str`
  - `ClusterConfig.source: str`, `.cluster_type: str`, `.enabled() -> list[str]`, `.roles() -> list[NodeSpec]`, `.nodes_map(cluster) -> dict`, `.children() -> dict[str, list[str]]`, `.sizing_by_role() -> dict[str, dict]`

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_clusterconfig.py`:

```python
CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G
  opensearch:
    enabled: false
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
  keycloak:
    enabled: true
"""


def config(text=CONFIG):
    return clusterconfig.from_text(text, "lab1_cluster.yaml")


def without(section):
    """CONFIG with one whole top-level section removed."""
    kept, dropping = [], False
    for line in CONFIG.splitlines():
        if line and not line.startswith(" "):
            dropping = line.startswith(f"{section}:")
        if not dropping:
            kept.append(line)
    return "\n".join(kept) + "\n"


# --- accessors ------------------------------------------------------------


def test_reads_the_cluster_type():
    assert config().cluster_type == "k8s"


def test_enabled_lists_only_the_enabled_technologies_in_file_order():
    assert config().enabled() == ["hdfs", "keycloak"]


def test_roles_prefix_technology_nodes_and_leave_cluster_nodes_alone():
    assert [r.role for r in config().roles()] == [
        "management", "compute", "hdfs-namenode", "hdfs-datanode",
    ]


def test_groups_replace_dashes_with_underscores():
    assert [r.group for r in config().roles()] == [
        "management", "compute", "hdfs_namenode", "hdfs_datanode",
    ]


def test_a_disabled_technology_contributes_no_roles():
    assert not [r for r in config().roles() if "opensearch" in r.role]


def test_nodes_map_numbers_every_vm_from_one():
    assert config().nodes_map("lab1") == {
        "lab1-management-1": {"cpus": 2, "memory": "4G", "disk": "20G"},
        "lab1-compute-1": {"cpus": 2, "memory": "3G", "disk": "20G"},
        "lab1-compute-2": {"cpus": 2, "memory": "3G", "disk": "20G"},
        "lab1-hdfs-namenode-1": {"cpus": 2, "memory": "4G", "disk": "20G"},
        "lab1-hdfs-datanode-1": {"cpus": 2, "memory": "4G", "disk": "40G"},
        "lab1-hdfs-datanode-2": {"cpus": 2, "memory": "4G", "disk": "40G"},
        "lab1-hdfs-datanode-3": {"cpus": 2, "memory": "4G", "disk": "40G"},
    }


def test_a_zero_count_role_builds_no_vms():
    text = CONFIG.replace("    count: 2\n    cpu: 2\n    memory: 3G", "    count: 0\n    cpu: 2\n    memory: 3G")
    assert not [n for n in config(text).nodes_map("lab1") if "compute" in n]


def test_children_group_the_cluster_nodes_and_each_enabled_technology():
    assert config().children() == {
        "k8s_nodes": ["management", "compute"],
        "hdfs_nodes": ["hdfs_namenode", "hdfs_datanode"],
    }


def test_keycloak_gets_no_children_group_because_it_owns_no_vms():
    assert "keycloak_nodes" not in config().children()


def test_sizing_by_role_keys_on_the_dashed_role():
    assert config().sizing_by_role()["hdfs-datanode"] == {
        "cpu": "2", "mem": "4G", "disk": "40G",
    }


# --- validation -----------------------------------------------------------


def test_rejects_an_unknown_cluster_type():
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        config(CONFIG.replace("type: k8s", "type: swarm"))


def test_rejects_a_missing_cluster_section():
    with pytest.raises(LabError, match="missing required key 'cluster.type'"):
        config(without("cluster"))


def test_rejects_a_missing_cluster_nodes_section():
    with pytest.raises(LabError, match="missing required key 'cluster_nodes'"):
        config(without("cluster_nodes"))


def test_requires_a_management_role():
    with pytest.raises(LabError, match="cluster_nodes must define 'management'"):
        config(CONFIG.replace("  management:", "  control:"))


def test_requires_at_least_one_management_node():
    text = CONFIG.replace(
        "  management:\n    count: 1", "  management:\n    count: 0"
    )
    with pytest.raises(LabError, match="cluster_nodes.management.count must be at least 1"):
        config(text)


def test_rejects_a_missing_node_field():
    text = CONFIG.replace("  compute:\n    count: 2\n    cpu: 2\n", "  compute:\n    count: 2\n")
    with pytest.raises(LabError, match="missing required key 'cluster_nodes.compute.cpu'"):
        config(text)


@pytest.mark.parametrize("bad", ["4Gi", "4", "4g", "big"])
def test_rejects_a_memory_value_multipass_would_not_take(bad):
    with pytest.raises(LabError, match=r"must look like 4G or 512M"):
        config(CONFIG.replace("memory: 3G", f"memory: {bad}"))


def test_rejects_a_non_numeric_count():
    with pytest.raises(LabError, match="must be a whole number"):
        config(CONFIG.replace("    count: 2", "    count: two"))


def test_rejects_a_cpu_below_one():
    with pytest.raises(LabError, match="cpu must be at least 1"):
        config(CONFIG.replace("  compute:\n    count: 2\n    cpu: 2", "  compute:\n    count: 2\n    cpu: 0"))


def test_rejects_an_unknown_technology():
    with pytest.raises(LabError, match=r"unknown technology 'kafka'; known: keycloak hdfs opensearch"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  kafka:\n    enabled: true"))


def test_rejects_a_technology_with_no_enabled_key():
    with pytest.raises(LabError, match="missing required key 'technologies.keycloak.enabled'"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  keycloak:\n    nodes:\n      x:\n        count: 1"))


def test_rejects_a_non_boolean_enabled():
    with pytest.raises(LabError, match="must be true or false"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  keycloak:\n    enabled: yes"))


def test_hdfs_must_have_exactly_one_namenode():
    text = CONFIG.replace(
        "      namenode:\n        count: 1", "      namenode:\n        count: 2"
    )
    with pytest.raises(LabError, match="hdfs has exactly one NameNode"):
        config(text)


def test_keycloak_must_not_declare_nodes():
    text = CONFIG.replace(
        "  keycloak:\n    enabled: true",
        "  keycloak:\n    enabled: true\n    nodes:\n      web:\n        count: 1\n        cpu: 1\n        memory: 1G\n        disk: 5G",
    )
    with pytest.raises(LabError, match="keycloak runs on the cluster and declares no nodes"):
        config(text)


def test_an_enabled_technology_that_owns_vms_must_declare_nodes():
    text = CONFIG.replace(
        "  opensearch:\n    enabled: false", "  opensearch:\n    enabled: true"
    ).replace(
        "    nodes:\n      master:\n        count: 3\n        cpu: 2\n        memory: 6G\n        disk: 40G\n",
        "",
    )
    with pytest.raises(LabError, match="technologies.opensearch must declare nodes"):
        config(text)


def test_a_disabled_technology_keeps_its_unvalidated_sizing():
    """`enabled: false` is how you park a technology without retyping it."""
    text = CONFIG.replace("        memory: 6G", "        memory: nonsense")
    assert config(text).enabled() == ["hdfs", "keycloak"]


def test_rejects_an_unknown_key_at_any_level():
    with pytest.raises(LabError, match=r"unknown key 'cluster_nodes.compute.memmory'"):
        config(CONFIG.replace("    memory: 3G", "    memmory: 3G"))


def test_rejects_an_unknown_top_level_section():
    with pytest.raises(LabError, match="unknown key 'extras'"):
        config(CONFIG + "extras:\n  a: 1\n")


# --- load() ---------------------------------------------------------------


def test_load_reads_the_named_file(configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    assert clusterconfig.load("lab1").cluster_type == "k8s"


def test_load_names_the_missing_path_and_what_does_exist(configs_dir):
    (configs_dir / "lab2_cluster.yaml").write_text(CONFIG)
    with pytest.raises(LabError, match="no config at .*lab1_cluster.yaml.*available: lab2"):
        clusterconfig.load("lab1")


def test_load_says_the_directory_is_empty_when_it_is(configs_dir):
    with pytest.raises(LabError, match="available: none"):
        clusterconfig.load("lab1")


def test_every_committed_config_is_valid():
    """A broken config must not be able to reach main."""
    from forgelab import paths

    for path in sorted(paths.CLUSTER_CONFIGS_DIR.glob("*_cluster.yaml")):
        clusterconfig.from_text(path.read_text(), str(path))
```

- [ ] **Step 2: Add the `configs_dir` fixture**

In `bamboo-specs/src/test/python/conftest.py`, replace the `clusters_dir` fixture with:

```python
@pytest.fixture
def configs_dir(tmp_path, monkeypatch):
    """An empty stand-in for cluster_configs/, for anything that resolves a
    cluster's config file."""
    from forgelab import paths

    monkeypatch.setattr(paths, "CLUSTER_CONFIGS_DIR", tmp_path)
    return tmp_path
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_clusterconfig.py -v`
Expected: FAIL — `AttributeError: module 'forgelab.clusterconfig' has no attribute 'from_text'`

- [ ] **Step 4: Point paths at the new directory**

In `bamboo-specs/src/main/java/lab/shared/python/forgelab/paths.py`, replace the line `CLUSTERS_DIR = SHARED_DIR / "clusters"` with:

```python
# <repo>/cluster_configs — committed input, unlike the generated registry, so
# this stays repo-relative: every Bamboo checkout carries it.
CLUSTER_CONFIGS_DIR = REPO_ROOT / "cluster_configs"
```

- [ ] **Step 5: Write the validation and the ClusterConfig class**

Append to `bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py`:

```python
CLUSTER_TYPES = ("k8s", "dcos")

# k9s is deliberately absent: it is a kubectl TUI, installed unconditionally by
# the k8s role, not something a cluster opts into.
TECHNOLOGIES = ("keycloak", "hdfs", "opensearch")

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
}

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


def _at(source, dotted, message):
    die(f"{source}: {message} ({dotted})" if dotted else f"{source}: {message}")


def _mapping(value, source, dotted):
    if not isinstance(value, dict):
        _at(source, "", f"'{dotted}' must be a block of keys, not a value")
    return value


def _require(parent, key, source, dotted):
    if key not in parent:
        _at(source, "", f"missing required key '{dotted}'")
    return parent[key]


def _reject_unknown(parent, allowed, source, prefix):
    for key in parent:
        if key not in allowed:
            dotted = f"{prefix}.{key}" if prefix else key
            _at(source, "", f"unknown key '{dotted}'; known: {' '.join(allowed)}")


def _int(parent, key, source, dotted, minimum):
    raw = _require(parent, key, source, dotted)
    if not isinstance(raw, str) or not raw.isdigit():
        _at(source, "", f"'{dotted}' must be a whole number (got {raw!r})")
    number = int(raw)
    if number < minimum:
        _at(source, "", f"'{dotted}' {key} must be at least {minimum} (got {number})")
    return number


def _size(parent, key, source, dotted):
    raw = _require(parent, key, source, dotted)
    if not isinstance(raw, str) or not _SIZE_RE.match(raw):
        _at(source, "", f"'{dotted}' must look like 4G or 512M (got {raw!r})")
    return raw


def _bool(parent, key, source, dotted):
    raw = _require(parent, key, source, dotted)
    if raw not in _BOOLS:
        _at(source, "", f"'{dotted}' must be true or false (got {raw!r})")
    return _BOOLS[raw]


def _node_spec(block, role, source, dotted):
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
        _at(source, "", f"cluster.type must be one of [{' '.join(CLUSTER_TYPES)}] "
                        f"(got {cluster_type!r})")

    nodes = _mapping(
        _require(data, "cluster_nodes", source, "cluster_nodes"), source, "cluster_nodes"
    )
    if CONTROL_ROLE not in nodes:
        _at(source, "", f"cluster_nodes must define '{CONTROL_ROLE}' — the k8s and "
                        f"dcos roles take their control node from it")
    cluster_roles = [
        _node_spec(block, role, source, f"cluster_nodes.{role}")
        for role, block in nodes.items()
    ]
    control = next(s for s in cluster_roles if s.role == CONTROL_ROLE)
    if control.count < 1:
        _at(source, "", f"cluster_nodes.{CONTROL_ROLE}.count must be at least 1")

    technologies = _mapping(data.get("technologies", {}), source, "technologies")
    enabled, tech_roles = [], {}
    for name, block in technologies.items():
        dotted = f"technologies.{name}"
        if name not in TECHNOLOGIES:
            _at(source, "", f"unknown technology {name!r}; "
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
                _at(source, "", f"{name} runs on the cluster and declares no nodes")
            continue
        if "nodes" not in block:
            _at(source, "", f"{dotted} must declare nodes when enabled")
        node_blocks = _mapping(block["nodes"], source, f"{dotted}.nodes")
        _reject_unknown(node_blocks, TECHNOLOGY_NODES[name], source, f"{dotted}.nodes")
        if not node_blocks:
            _at(source, "", f"{dotted} must declare nodes when enabled")
        tech_roles[name] = [
            _node_spec(spec, f"{name}-{node}", source, f"{dotted}.nodes.{node}")
            for node, spec in node_blocks.items()
        ]

    if "hdfs" in enabled:
        namenodes = [s for s in tech_roles["hdfs"] if s.role == "hdfs-namenode"]
        if len(namenodes) != 1 or namenodes[0].count != 1:
            _at(source, "", "hdfs has exactly one NameNode: "
                            "technologies.hdfs.nodes.namenode.count must be 1")

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
```

Add `NamedTuple` to the module's imports: the `from typing import NamedTuple` line goes beside `import re`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_clusterconfig.py -v`
Expected: PASS. `test_every_committed_config_is_valid` passes vacuously for now — `cluster_configs/` does not exist yet, so the glob yields nothing.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/clusterconfig.py \
        bamboo-specs/src/main/java/lab/shared/python/forgelab/paths.py \
        bamboo-specs/src/test/python/test_clusterconfig.py \
        bamboo-specs/src/test/python/conftest.py
git commit -m "feat: validate cluster configs and derive nodes, groups and sizing"
```

---

### Task 3: Shrink planvars to name plus config

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/planvars.py` (rewrite)
- Test: `bamboo-specs/src/test/python/test_planvars.py` (rewrite)

**Interfaces:**
- Consumes: `clusterconfig.load` from Task 2.
- Produces:
  - `planvars.require_cluster_name(cluster, usage) -> str` — unchanged behaviour
  - `planvars.resolve(cluster, config_name, usage) -> (str, ClusterConfig)` — `config_name` empty or whitespace means "use the cluster name"

- [ ] **Step 1: Replace the test file**

Overwrite `bamboo-specs/src/test/python/test_planvars.py`:

```python
"""Plan variables: the cluster name, and which config a run builds from."""

import pytest

from forgelab import planvars
from forgelab.proc import LabError

CONFIG = """
cluster:
  type: dcos

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
"""


# --- cluster_name ---------------------------------------------------------


def test_accepts_a_lowercase_name():
    assert planvars.require_cluster_name("lab-1", "usage") == "lab-1"


def test_rejects_an_empty_name_with_the_usage_line():
    with pytest.raises(LabError, match="usage"):
        planvars.require_cluster_name("", "usage")


@pytest.mark.parametrize("name", ["Lab1", "lab_1", "lab.1", "lab 1"])
def test_rejects_a_malformed_name(name):
    with pytest.raises(LabError, match="cluster_name must match"):
        planvars.require_cluster_name(name, "usage")


def test_names_the_offending_value():
    with pytest.raises(LabError, match="got 'Lab1'"):
        planvars.require_cluster_name("Lab1", "usage")


# --- resolve() ------------------------------------------------------------


def test_resolve_returns_the_name_and_the_loaded_config(configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    cluster, config = planvars.resolve("lab1", "", "usage")
    assert cluster == "lab1"
    assert config.cluster_type == "dcos"


def test_an_empty_config_variable_means_the_cluster_name(configs_dir):
    """Bamboo always passes ${bamboo.cluster_config}, which may be empty."""
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    _, config = planvars.resolve("lab1", "  ", "usage")
    assert config.source.endswith("lab1_cluster.yaml")


def test_the_config_variable_selects_a_different_file(configs_dir):
    (configs_dir / "big_cluster.yaml").write_text(CONFIG)
    _, config = planvars.resolve("lab1", "big", "usage")
    assert config.source.endswith("big_cluster.yaml")


def test_resolve_checks_the_name_before_reading_any_file(configs_dir):
    """No configs exist here — a bad name must fail on the name, not the read."""
    with pytest.raises(LabError, match="cluster_name must match"):
        planvars.resolve("Lab1", "", "usage")


def test_resolve_rejects_a_malformed_config_name(configs_dir):
    with pytest.raises(LabError, match="cluster_config must match"):
        planvars.resolve("lab1", "Big Cluster", "usage")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_planvars.py -v`
Expected: FAIL — `TypeError: resolve() missing 1 required positional argument`

- [ ] **Step 3: Rewrite planvars.py**

Overwrite `bamboo-specs/src/main/java/lab/shared/python/forgelab/planvars.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_planvars.py bamboo-specs/src/test/python/test_clusterconfig.py -v`
Expected: PASS. Other test files still fail — later tasks fix them.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/planvars.py \
        bamboo-specs/src/test/python/test_planvars.py
git commit -m "refactor: reduce plan variables to cluster_name and cluster_config"
```

---

### Task 4: Terraform takes one nodes map

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/terraform/variables.tf` (rewrite)
- Modify: `bamboo-specs/src/main/java/lab/shared/terraform/main.tf`
- Modify: `bamboo-specs/src/main/java/lab/shared/terraform/outputs.tf`

**Interfaces:**
- Produces: a `nodes` variable of type `map(object({cpus = number, memory = string, disk = string}))`, required. `provision.py` (Task 7) supplies it as `.tfvars.json`.

- [ ] **Step 1: Rewrite variables.tf**

Overwrite `bamboo-specs/src/main/java/lab/shared/terraform/variables.tf`:

```hcl
variable "cluster_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.cluster_name))
    error_message = "cluster_name must match ^[a-z0-9-]+$."
  }
}

# Every VM of the cluster, keyed by name, expanded from the cluster's YAML
# config by forgelab/clusterconfig.py. Deliberately has no default: a stray
# `terraform apply` without a var-file must fail rather than resolve this to an
# empty map and destroy the whole workspace.
variable "nodes" {
  type = map(object({
    cpus   = number
    memory = string
    disk   = string
  }))
}

variable "backend" {
  type    = string
  default = "multipass"
}

variable "image" {
  type    = string
  default = "noble"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.forgelab/id_ed25519.pub"
}
```

- [ ] **Step 2: Rewrite main.tf's locals and module block**

In `bamboo-specs/src/main/java/lab/shared/terraform/main.tf`, delete the entire `locals { ... }` block and replace the `module "vms"` block, leaving `terraform {}` and `resource "local_file" "cloud_init"` untouched:

```hcl
module "vms" {
  source         = "./modules/multipass"
  nodes          = var.nodes
  image          = var.image
  cloudinit_file = local_file.cloud_init.filename
}
```

- [ ] **Step 3: Rewrite outputs.tf**

Overwrite `bamboo-specs/src/main/java/lab/shared/terraform/outputs.tf`:

```hcl
output "node_names" {
  description = "All VM names, in config order"
  value       = keys(var.nodes)
}
```

- [ ] **Step 4: Verify Terraform still formats and validates**

Run:
```bash
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform fmt -check -recursive
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform init -backend=false -input=false
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform validate
```
Expected: `fmt` prints nothing, `validate` prints "Success! The configuration is valid."

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/terraform/
git commit -m "refactor: give terraform one nodes map instead of per-role variables"
```

---

### Task 5: Config-driven inventory groups

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/inventory.py:14-58`
- Test: `bamboo-specs/src/test/python/test_inventory.py`

**Interfaces:**
- Consumes: `ClusterConfig.children()` from Task 2.
- Produces: `inventory.render(cluster, groups, children) -> str` — `groups` is `{group_name: [multipass.Node]}`, `children` is `{child_group: [group_name]}`. `inventory.mgmt_ip` is renamed to `inventory.control_ip` and reads the `management` group.

- [ ] **Step 1: Update the test file**

In `bamboo-specs/src/test/python/test_inventory.py`, make these replacements throughout:

- `MGMT = [Node("lab1-mgmt-1", ...)]` becomes `MANAGEMENT = [Node("lab1-management-1", ["192.168.252.10", "10.244.0.1"])]`, and every use of `MGMT` becomes `MANAGEMENT`.
- `NAMENODE`/`DATANODE` node names become `lab1-hdfs-namenode-1`, `lab1-hdfs-datanode-1`, `lab1-hdfs-datanode-2`; `OPENSEARCH` becomes `[Node("lab1-opensearch-master-1", ["192.168.252.31"])]`.
- Group keys in `bare()` and `loaded()` become `management`, `compute`, `hdfs_namenode`, `hdfs_datanode`, `opensearch_master`.
- Every `inventory.render("lab1", X)` call becomes `inventory.render("lab1", X, CHILDREN)`.
- `inventory.mgmt_ip` becomes `inventory.control_ip`.

Add at the top of the file, after the `Node` imports:

```python
CHILDREN = {
    "k8s_nodes": ["management", "compute"],
    "hdfs_nodes": ["hdfs_namenode", "hdfs_datanode"],
    "opensearch_nodes": ["opensearch_master"],
}
```

Replace `test_render_produces_the_expected_inventory` with:

```python
def test_render_produces_the_expected_inventory():
    assert inventory.render("lab1", bare(), CHILDREN) == (
        "[management]\n"
        "lab1-management-1 ansible_host=192.168.252.10\n"
        "\n"
        "[compute]\n"
        "lab1-compute-1 ansible_host=192.168.252.11\n"
        "lab1-compute-2 ansible_host=192.168.252.12\n"
        "\n"
        "[hdfs_namenode]\n"
        "\n"
        "[hdfs_datanode]\n"
        "\n"
        "[opensearch_master]\n"
        "\n"
        "[k8s_nodes:children]\n"
        "management\n"
        "compute\n"
        "\n"
        "[hdfs_nodes:children]\n"
        "hdfs_namenode\n"
        "hdfs_datanode\n"
        "\n"
        "[opensearch_nodes:children]\n"
        "opensearch_master\n"
        "\n"
        "[all:vars]\n"
        "ansible_user=ubuntu\n"
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519\n"
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'\n"
        "cluster_name=lab1\n"
    )
```

Replace `test_render_preserves_the_callers_group_order`'s expected headers with:

```python
    assert headers == [
        "[management]",
        "[compute]",
        "[hdfs_namenode]",
        "[hdfs_datanode]",
        "[opensearch_master]",
        "[k8s_nodes:children]",
        "[hdfs_nodes:children]",
        "[opensearch_nodes:children]",
        "[all:vars]",
    ]
```

Add:

```python
def test_render_emits_only_the_children_it_is_given():
    """A cluster with no technologies has no <name>_nodes groups at all."""
    text = inventory.render("lab1", bare(), {"k8s_nodes": ["management", "compute"]})
    assert "[hdfs_nodes:children]" not in text
    assert "[k8s_nodes:children]" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_inventory.py -v`
Expected: FAIL — `TypeError: render() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Rewrite the top of inventory.py**

In `bamboo-specs/src/main/java/lab/shared/python/forgelab/inventory.py`, delete the `K8S_GROUPS` and `HDFS_GROUPS` constants and replace `render` and `mgmt_ip`:

```python
def render(cluster: str, groups, children) -> str:
    """Build the .ini for a cluster from an ordered {group: [Node]} mapping.

    Empty groups are still emitted: a `hosts: hdfs_datanode` play must resolve
    to zero hosts rather than fail on a group ansible has never heard of. Group
    order is the caller's dict order, but nodes within a group are always sorted
    by natural-sort name — see `_natural_key`.

    `children` is the {child: [group]} mapping the cluster's config derives —
    k8s_nodes for the cluster nodes, <technology>_nodes for each enabled
    technology that owns VMs. Nothing here knows which technologies exist.
    """
    lines = []
    for group, nodes in groups.items():
        lines.append(f"[{group}]")
        ordered = sorted(nodes, key=lambda n: _natural_key(n.name))
        lines += [f"{n.name} ansible_host={multipass.lan_ip(n)}" for n in ordered]
        lines.append("")
    for child, members in children.items():
        lines += [f"[{child}:children]", *members, ""]
    lines += [
        "[all:vars]",
        "ansible_user=ubuntu",
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'",
        f"cluster_name={cluster}",
    ]
    return "\n".join(lines) + "\n"
```

and, replacing `mgmt_ip`:

```python
def control_ip(text: str) -> str:
    """The first management-group host address, or "" when the group is empty."""
    return first_ip(text, CONTROL_GROUP)
```

Add the import and constant near the top, beside the existing imports:

```python
from . import multipass
from .clusterconfig import CONTROL_ROLE
from .proc import die

# The ansible group the k8s and dcos roles take their control node from.
CONTROL_GROUP = CONTROL_ROLE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_inventory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/inventory.py \
        bamboo-specs/src/test/python/test_inventory.py
git commit -m "refactor: derive inventory groups from the cluster config"
```

---

### Task 6: Registry reads roles off dashed names

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/registry.py:83-104`
- Test: `bamboo-specs/src/test/python/test_registry.py`

**Interfaces:**
- Consumes: `ClusterConfig.sizing_by_role()` from Task 2.
- Produces: `registry.nodes_from(cluster, hosts, sizing) -> list[Node]` where `sizing` is now `{role: {"cpu", "mem", "disk"}}` — a nested mapping, not the old flat `<role>_cpu` keys.

- [ ] **Step 1: Update the test file**

In `bamboo-specs/src/test/python/test_registry.py`:

Replace the `SIZING` and `HOSTS` constants:

```python
SIZING = {
    "management": {"cpu": "2", "mem": "4G", "disk": "20G"},
    "compute": {"cpu": "2", "mem": "3G", "disk": "20G"},
}

HOSTS = [
    ("lab1-management-1", "192.168.252.10"),
    ("lab1-compute-1", "192.168.252.11"),
]
```

In `test_renders_the_whole_file`, change `example: ssh lab1-mgmt-1` to `example: ssh lab1-management-1`, the first node's `- name: lab1-mgmt-1` / `role: mgmt` to `- name: lab1-management-1` / `role: management`, and its `mem: 4G` line stays.

In `test_quotes_only_what_bare_yaml_would_change`, replace the three `lab1-mgmt-1` literals with `lab1-management-1`.

In `test_renders_empty_collections_as_flow_sequences`, the expected line `assert "example: ssh lab1-mgmt-1" in text` becomes `assert "example: ssh lab1-management-1" in text`. That value comes from `registry.render`'s fallback, which Step 3 changes.

Replace the three sizing tests with:

```python
def test_reads_role_and_sizing_for_each_node():
    management, compute = registry.nodes_from(HOSTS, SIZING)
    assert (management.role, management.ip, management.mem) == (
        "management", "192.168.252.10", "4G",
    )
    assert (compute.role, compute.disk) == ("compute", "20G")


def test_reads_a_dashed_technology_role_off_the_name():
    """`lab1-hdfs-datanode-2` is role `hdfs-datanode`, not `datanode`."""
    sizing = {
        "hdfs-namenode": {"cpu": "2", "mem": "4G", "disk": "20G"},
        "hdfs-datanode": {"cpu": "2", "mem": "4G", "disk": "40G"},
    }
    hosts = [
        ("lab1-hdfs-namenode-1", "192.168.252.20"),
        ("lab1-hdfs-datanode-2", "192.168.252.22"),
    ]
    namenode, datanode = registry.nodes_from(hosts, sizing)
    assert (namenode.role, namenode.disk) == ("hdfs-namenode", "20G")
    assert (datanode.role, datanode.disk) == ("hdfs-datanode", "40G")


def test_omits_sizing_the_config_does_not_carry():
    text = registry.render(
        "lab1", "k8s", "2026-08-03T14:22:11Z", registry.nodes_from(HOSTS, {}), []
    )
    assert "cpu:" not in text
    assert "role: management" in text
```

In `test_the_rendered_file_parses_as_yaml`, change the expected second node to `"name": "lab1-compute-1"` with `"role": "compute"` (unchanged) — only the first node's name changed, so no edit is needed there beyond what the constants already do.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_registry.py -v`
Expected: FAIL — `AssertionError` on `role`, which is `""` because `parts[-2]` reads `hdfs` from `lab1-hdfs-datanode-2`.

- [ ] **Step 3: Rewrite nodes_from and the ssh example fallback**

In `bamboo-specs/src/main/java/lab/shared/python/forgelab/registry.py`, add near the other module constants:

```python
_INDEXED_RE = re.compile(r"^(?P<role>.+)-\d+$")
```

with `import re` beside `import json`. Replace `nodes_from`:

```python
def nodes_from(cluster: str, hosts, sizing: dict) -> list:
    """Build the node list from inventory [(name, ip)] pairs and config sizing.

    The role comes from the node's own name (`<cluster>-<role>-<n>`, the shape
    the terraform module builds) and keys the sizing lookup. Both the cluster
    name and the role may contain dashes — cluster names are `^[a-z0-9-]+$`, and
    a technology's nodes are prefixed with their technology, so
    `lab-1-hdfs-datanode-2` is cluster `lab-1` and role `hdfs-datanode`. Only
    stripping the cluster's own prefix tells the two apart; splitting on a dash
    cannot.

    A name that does not fit the shape yields an empty role and no sizing rather
    than failing: the cluster is already built and verified by the time this
    runs, and a malformed name is a bug worth seeing in the file, not a reason
    to throw the registry entry away.
    """
    prefix = f"{cluster}-"
    nodes = []
    for name, ip in hosts:
        role = ""
        match = _INDEXED_RE.match(name)
        if match and match.group("role").startswith(prefix):
            role = match.group("role")[len(prefix):]
        size = sizing.get(role, {})
        nodes.append(
            Node(
                name=name,
                role=role,
                ip=ip,
                cpu=size.get("cpu", ""),
                mem=size.get("mem", ""),
                disk=size.get("disk", ""),
            )
        )
    return nodes
```

In `render`, change the ssh example fallback from `f"ssh {cluster}-mgmt-1"` to `f"ssh {cluster}-{CONTROL_ROLE}-1"`, importing `CONTROL_ROLE`:

```python
from .clusterconfig import CONTROL_ROLE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/registry.py \
        bamboo-specs/src/test/python/test_registry.py
git commit -m "refactor: read node roles off dashed VM names and config sizing"
```

---

### Task 7: Rewire provision.py

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py` (rewrite)
- Test: `bamboo-specs/src/test/python/test_provision.py` (rewrite)

**Interfaces:**
- Consumes: `planvars.resolve` (Task 3), `ClusterConfig.nodes_map/.children/.roles/.sizing_by_role/.enabled` (Task 2), `inventory.render(cluster, groups, children)` (Task 5), `registry.nodes_from(cluster, hosts, sizing)` (Task 6), `install.run(cluster, config)` (Task 8).
- Produces:
  - `provision.write_tfvars(cluster, config) -> Path` — writes `terraform/.generated/<cluster>.tfvars.json`, kept after the run
  - `provision.group_nodes(cluster, config) -> dict` — `{group: [multipass.Node]}`
  - `terraform/.generated/<cluster>.tfvars.json` containing `{"cluster_name": ..., "nodes": {...}}`

- [ ] **Step 1: Replace the test file**

Overwrite `bamboo-specs/src/test/python/test_provision.py`:

```python
"""Validation gates and stage order for provision, with externals stubbed."""

import json
from pathlib import Path

import pytest

import provision
from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 1
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 2
        cpu: 2
        memory: 4G
        disk: 40G
  keycloak:
    enabled: false
"""

BUILT = {
    "lab1-management-1": "192.168.252.10",
    "lab1-compute-1": "192.168.252.11",
    "lab1-hdfs-namenode-1": "192.168.252.20",
    "lab1-hdfs-datanode-1": "192.168.252.21",
    "lab1-hdfs-datanode-2": "192.168.252.22",
}


@pytest.fixture
def lab(tmp_path, monkeypatch, configs_dir):
    calls = []
    existing = {"vms": []}

    class FakeMultipass:
        def list_vms(self, prefix=""):
            return [n for n in existing["vms"] if n.name.startswith(prefix)]

    class FakeTerraform:
        def init(self):
            calls.append(("tf-init",))

        def workspace_select_or_new(self, name):
            calls.append(("tf-workspace", name))

        def apply_retry(self, *args):
            calls.append(("tf-apply", *args))
            # The VMs only exist once terraform has applied — the inventory is
            # rendered from backend state afterwards. The fake builds exactly
            # the nodes map it was handed, which is what the real apply does.
            varsfile = next(a[len("-var-file="):] for a in args
                            if a.startswith("-var-file="))
            payload = json.loads(Path(varsfile).read_text())
            existing["vms"] = [
                Node(name, [BUILT[name]]) for name in payload["nodes"]
            ]

    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    inv_dir = tmp_path / "inventory"
    registry_dir = tmp_path / "cluster_registered"

    monkeypatch.setattr(provision, "multipass", FakeMultipass())
    monkeypatch.setattr(provision, "terraform", FakeTerraform())
    monkeypatch.setattr(provision.proc, "require_tools", lambda *_: None)
    monkeypatch.setattr(provision.paths, "TF_DIR", tmp_path / "terraform")
    monkeypatch.setattr(provision.paths, "INV_DIR", inv_dir)
    # Same forgelab.paths module install.py and credentials.py read from too —
    # without this, install.run()'s credentials.write() would land in the
    # real ~/.forgelab instead of the test's tmp_path.
    monkeypatch.setattr(provision.paths, "FORGELAB_HOME", tmp_path / "home")
    monkeypatch.setattr(
        provision.sshconf, "write", lambda c, h: calls.append(("ssh", c, tuple(h)))
    )
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: calls.append(("run", str(a[0])))
    )
    monkeypatch.setattr(provision.registry.paths, "REGISTRY_DIR", registry_dir)
    real_write = provision.registry.write

    def spy_write(*args, **kwargs):
        calls.append(("registry", args[0]))
        return real_write(*args, **kwargs)

    monkeypatch.setattr(provision.registry, "write", spy_write)

    return type(
        "Lab",
        (),
        {
            "calls": calls,
            "existing": existing,
            "configs": configs_dir,
            "tf_dir": tmp_path / "terraform",
            "inv_dir": inv_dir,
            "registry_dir": registry_dir,
        },
    )


# --- validation gates -----------------------------------------------------


def test_requires_a_cluster_name(lab):
    with pytest.raises(LabError, match="usage:"):
        provision.main([])


@pytest.mark.parametrize("name", ["Lab1", "lab_1"])
def test_rejects_a_malformed_cluster_name(lab, name):
    with pytest.raises(LabError, match=r"cluster_name must match"):
        provision.main([name])


def test_refuses_to_provision_over_existing_vms(lab):
    lab.existing["vms"] = [Node("lab1-management-1", ["1.2.3.4"])]
    with pytest.raises(LabError, match="already exist; deprovision first"):
        provision.main(["lab1"])


def test_rejects_a_missing_config(lab):
    with pytest.raises(LabError, match="no config at .*nosuch_cluster.yaml"):
        provision.main(["nosuch"])


def test_rejects_an_invalid_config(lab):
    (lab.configs / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        provision.main(["lab1"])


def test_validates_before_touching_the_backend(lab, monkeypatch):
    """A bad plan variable must not cost a multipass round trip.

    The Validate stage catches this earlier still, but `make provision` has no
    stages — this ordering is what makes the CLI fail as fast as the plan.
    """

    def explode(*_args, **_kwargs):
        raise AssertionError("multipass was queried before validation finished")

    monkeypatch.setattr(provision.multipass, "list_vms", explode)
    with pytest.raises(LabError, match="no config at"):
        provision.main(["lab1", "nosuch"])


def test_the_config_variable_selects_a_different_file(lab):
    (lab.configs / "big_cluster.yaml").write_text(CONFIG)
    provision.main(["lab1", "big"])
    assert (lab.tf_dir / ".generated" / "lab1.tfvars.json").is_file()


# --- the generated tfvars -------------------------------------------------


def test_writes_the_nodes_map_terraform_applies(lab):
    provision.main(["lab1"])
    payload = json.loads(
        (lab.tf_dir / ".generated" / "lab1.tfvars.json").read_text()
    )
    assert payload["cluster_name"] == "lab1"
    assert sorted(payload["nodes"]) == sorted(BUILT)
    assert payload["nodes"]["lab1-hdfs-datanode-1"] == {
        "cpus": 2, "memory": "4G", "disk": "40G",
    }


def test_applies_with_the_generated_var_file(lab):
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert f"-var-file={lab.tf_dir / '.generated' / 'lab1.tfvars.json'}" in apply


def test_the_generated_var_file_survives_the_run(lab):
    """deprovision reads it, so teardown never needs the config file."""
    provision.main(["lab1"])
    assert (lab.tf_dir / ".generated" / "lab1.tfvars.json").is_file()


def test_a_disabled_technology_builds_no_vms(lab):
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    (lab.configs / "lab1_cluster.yaml").write_text(text)
    provision.main(["lab1"])
    payload = json.loads(
        (lab.tf_dir / ".generated" / "lab1.tfvars.json").read_text()
    )
    assert sorted(payload["nodes"]) == ["lab1-compute-1", "lab1-management-1"]


# --- stage order ----------------------------------------------------------


def test_runs_the_stages_in_order(lab):
    provision.main(["lab1"])
    kinds = [c[0] for c in lab.calls]
    assert kinds.index("tf-init") < kinds.index("tf-workspace") < kinds.index("tf-apply")
    assert kinds.index("tf-apply") < kinds.index("ssh") < kinds.index("run")


def test_writes_the_inventory_before_running_ansible(lab):
    provision.main(["lab1"])
    assert (lab.inv_dir / "lab1.ini").is_file()


def test_registers_the_cluster_only_after_verify(lab):
    provision.main(["lab1"])
    kinds = [c[0] for c in lab.calls]
    # two runs: ansible-playbook, then verify.py — registration comes after both
    assert kinds[-1] == "registry"


def test_leaves_no_cluster_info_when_verify_fails(lab, monkeypatch):
    def fail_on_verify(*args, **kwargs):
        if str(args[0]) != "ansible-playbook":
            raise LabError("nodes not all Ready within timeout")

    monkeypatch.setattr(provision.proc, "run", fail_on_verify)
    with pytest.raises(LabError):
        provision.main(["lab1"])
    assert not (lab.registry_dir / "lab1_cluster_info.yml").exists()


# --- inventory and registry -----------------------------------------------


def test_technology_vms_land_in_their_prefixed_groups(lab):
    provision.main(["lab1"])
    text = (lab.inv_dir / "lab1.ini").read_text()
    assert inventory.group_ips(text, "hdfs_namenode") == ["192.168.252.20"]
    assert inventory.group_ips(text, "hdfs_datanode") == [
        "192.168.252.21", "192.168.252.22",
    ]


def test_the_inventory_carries_the_derived_children(lab):
    provision.main(["lab1"])
    text = (lab.inv_dir / "lab1.ini").read_text()
    assert "[k8s_nodes:children]\nmanagement\ncompute\n" in text
    assert "[hdfs_nodes:children]\nhdfs_namenode\nhdfs_datanode\n" in text


def test_writes_the_cluster_info_file(lab):
    provision.main(["lab1"])
    info = (lab.registry_dir / "lab1_cluster_info.yml").read_text()
    assert "cluster: lab1" in info
    assert "ip: 192.168.252.10" in info
    assert "mem: 4G" in info


def test_the_cluster_info_tells_the_namenode_from_the_datanodes(lab):
    provision.main(["lab1"])
    info = (lab.registry_dir / "lab1_cluster_info.yml").read_text()
    assert "- name: lab1-hdfs-namenode-1\n    role: hdfs-namenode\n" in info
    assert "- name: lab1-hdfs-datanode-1\n    role: hdfs-datanode\n" in info
    assert "disk: 20G" in info
    assert "disk: 40G" in info


# --- what ansible is told -------------------------------------------------


def _capture_extra_vars(monkeypatch):
    """Patch install's ansible-playbook call and return the payload it wrote —
    read from the @varsfile JSON while the file still exists, since
    install.run() deletes it in a `finally` block.

    provision.install.proc and provision.proc are the same forgelab.proc module
    object, so this fake also receives the verify.py call; only the
    ansible-playbook call has an "@varsfile" argument, so anything else is left
    alone (a no-op stand-in, same as the rest of the suite's fakes).
    """
    seen = {}

    def fake_run(*args, **kwargs):
        varsfile = next((str(a)[1:] for a in args if str(a).startswith("@")), None)
        if varsfile is not None:
            seen["payload"] = json.loads(Path(varsfile).read_text())

    monkeypatch.setattr(provision.install.proc, "run", fake_run)
    return seen


def test_tells_ansible_where_to_report_components(lab, monkeypatch):
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert "component_report" in seen["payload"]


def test_passes_the_configs_cluster_type_to_ansible(lab, monkeypatch):
    (lab.configs / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: dcos"))
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["cluster_type"] == "dcos"


def test_passes_the_enabled_technologies_as_addons(lab, monkeypatch):
    """site.yml still gates its roles on the `addons` comma string."""
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["addons"] == "hdfs"


def test_a_config_with_nothing_enabled_passes_an_empty_addons(lab, monkeypatch):
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    (lab.configs / "lab1_cluster.yaml").write_text(text)
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["addons"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_provision.py -v`
Expected: FAIL — `AttributeError: module 'provision' has no attribute 'write_tfvars'`

- [ ] **Step 3: Rewrite provision.py**

Overwrite `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py`:

```python
#!/usr/bin/env python3
"""Provision a lab cluster: validate, terraform apply, install, verify."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, inventory, multipass, paths, planvars, proc, registry, sshconf,
    terraform,
)

import install  # noqa: E402

USAGE = "usage: provision.py <cluster_name> [cluster_config]"


def write_tfvars(cluster: str, config) -> Path:
    """Write the cluster's generated .tfvars.json and return its path.

    Terraform reads .tfvars.json natively, so the whole config reaches it as one
    file rather than a command line of -var flags — and a failed run leaves that
    file behind to show exactly what was applied.

    Kept after the run rather than deleted: deprovision.py reads it, so a
    teardown never depends on the cluster's config file still existing.
    """
    generated = paths.TF_DIR / ".generated"
    generated.mkdir(parents=True, exist_ok=True)
    path = generated / f"{cluster}.tfvars.json"
    payload = {"cluster_name": cluster, "nodes": config.nodes_map(cluster)}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def group_nodes(cluster: str, config) -> dict:
    """{group: [multipass.Node]} for every role the config declares.

    Rendered from live backend state because the Terraform multipass provider
    does not expose VM addresses. Roles with no VMs still get their group, so a
    play targeting one resolves to zero hosts instead of failing.
    """
    return {
        spec.group: multipass.list_vms(f"{cluster}-{spec.role}-")
        for spec in config.roles()
    }


def main(argv):
    # Stage 1: Validate. The plan variables and the whole config first, before a
    # tool lookup or a multipass round trip, so a typo costs seconds. The
    # Validate stage of the PROV plan runs the same checks earlier still, on any
    # agent.
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    proc.require_tools("terraform", "multipass", "ansible-playbook", "ssh")
    if multipass.list_vms(f"{cluster}-"):
        proc.die(f"VMs with prefix '{cluster}-' already exist; deprovision first")

    print(
        f"==> provisioning '{cluster}' type={config.cluster_type} "
        f"technologies={','.join(config.enabled()) or 'none'} "
        f"config={Path(config.source).name}"
    )

    # Stage 2: Provision (workspace per cluster, one generated var-file)
    tfvars = write_tfvars(cluster, config)
    terraform.init()
    terraform.workspace_select_or_new(cluster)
    terraform.apply_retry(f"-var-file={tfvars}", "-input=false")

    # Render inventory from live multipass state (provider does not expose IPs)
    paths.INV_DIR.mkdir(parents=True, exist_ok=True)
    inv = paths.INV_DIR / f"{cluster}.ini"
    inv.write_text(
        inventory.render(cluster, group_nodes(cluster, config), config.children())
    )
    inventory.assert_unique_ips(inv)
    print(f"==> inventory: {inv}")
    sshconf.write(cluster, inventory.parse_hosts(inv.read_text()))

    # Stage 3: Install. The roles report what they installed into this file.
    report = install.run(cluster, config)

    # Stage 4: Verify
    proc.run(
        sys.executable,
        Path(__file__).resolve().parent / "verify.py",
        cluster,
        config.cluster_type,
        ",".join(config.enabled()),
    )

    # Stage 5: Register. Last, so the registry only lists healthy clusters.
    registry.write(
        cluster,
        config.cluster_type,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        registry.nodes_from(
            cluster, inventory.parse_hosts(inv.read_text()), config.sizing_by_role()
        ),
        registry.read_components(report),
        credentials=(
            credentials.path(cluster) if credentials.path(cluster).is_file() else ""
        ),
    )
    print(f"==> cluster '{cluster}' provisioned and verified")


if __name__ == "__main__":
    proc.main(main)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_provision.py -v`
Expected: PASS — Task 8 supplies `install.run(cluster, config)`; if it has not landed yet, run Task 8 first and return here.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_provision.py
git commit -m "refactor: drive provision from the cluster config and a generated tfvars.json"
```

---

### Task 8: install.py and the Validate stage

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/install.py`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py` (rewrite)
- Test: `bamboo-specs/src/test/python/test_install.py`
- Test: `bamboo-specs/src/test/python/test_validate_prov.py` (rewrite)

**Interfaces:**
- Consumes: `planvars.resolve` (Task 3), `ClusterConfig` (Task 2).
- Produces:
  - `install.run(cluster, config) -> Path` — the component report path
  - `install.extra_vars(cluster_type, technologies, report, secret_values) -> dict` — unchanged signature
  - `validate_prov.report(cluster, config) -> list[str]`

- [ ] **Step 1: Update test_install.py**

In `bamboo-specs/src/test/python/test_install.py`:

Add the config text and a helper after the imports:

```python
CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G

technologies:
  keycloak:
    enabled: true
"""


def a_config(text=CONFIG):
    from forgelab import clusterconfig

    return clusterconfig.from_text(text, "lab1_cluster.yaml")


NO_TECH = CONFIG.replace("    enabled: true", "    enabled: false")
```

Change the inventory line in the `lab` fixture to `(inv_dir / "lab1.ini").write_text("[management]\nlab1-management-1 ansible_host=1.2.3.4\n")`.

Replace every `install.run("lab1", "k8s", ["keycloak"])` with `install.run("lab1", a_config())`, every `install.run("lab1", "k8s", [])` with `install.run("lab1", a_config(NO_TECH))`, and `install.run("nosuch", "k8s", [])` with `install.run("nosuch", a_config())`.

`test_run_writes_no_credentials_file_when_nothing_needs_one` uses `install.run("lab1", a_config(NO_TECH))`.

Replace the three `main` tests with:

```python
def test_main_rejects_an_invalid_config(lab, configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        install.main(["lab1"])


def test_main_rejects_a_missing_config(lab, configs_dir):
    with pytest.raises(LabError, match="no config at"):
        install.main(["lab1"])


def test_main_rejects_a_malformed_cluster_name(lab, configs_dir):
    """`make addons` gets the same gate the plan's Validate stage applies."""
    with pytest.raises(LabError, match="cluster_name must match"):
        install.main(["Lab1"])
```

- [ ] **Step 2: Replace test_validate_prov.py**

Overwrite `bamboo-specs/src/test/python/test_validate_prov.py`:

```python
"""The PROV plan's Validate stage: what it reports and what it rejects."""

import pytest

import validate_prov as validate
from forgelab import clusterconfig
from forgelab.proc import LabError

CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 3
        cpu: 4
        memory: 8G
        disk: 40G
  opensearch:
    enabled: false
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
"""


def lines(text=CONFIG):
    return validate.report("lab1", clusterconfig.from_text(text, "lab1_cluster.yaml"))


def test_reports_the_name_type_technologies_and_config():
    out = "\n".join(lines())
    assert "==> cluster_name   lab1" in out
    assert "==> cluster_type   k8s" in out
    assert "==> technologies   hdfs" in out
    assert "==> config         lab1_cluster.yaml" in out


def test_reports_no_technologies_as_none():
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    assert "==> technologies   none" in "\n".join(lines(text))


def rollup_row(role, text=CONFIG):
    """A roll-up line as its whitespace-separated columns."""
    for line in lines(text):
        columns = line.split()
        if columns and columns[0] == role:
            return columns
    raise AssertionError(f"no roll-up row for {role}")


def test_rolls_up_every_role_with_its_sizing():
    assert rollup_row("management") == ["management", "1", "2", "4G", "20G"]
    assert rollup_row("hdfs-datanode") == ["hdfs-datanode", "3", "4", "8G", "40G"]


def test_the_roll_up_has_a_header():
    assert ["ROLE", "N", "CPU", "MEM", "DISK"] in [l.split() for l in lines()]


def test_a_disabled_technology_is_absent_from_the_roll_up():
    assert "opensearch" not in "\n".join(lines())


def test_totals_the_vms_cpu_and_memory():
    """1x2 + 2x2 + 1x2 + 3x4 = 20 vCPU; 4 + 2x3 + 4 + 3x8 = 38G; 7 VMs."""
    out = "\n".join(lines())
    assert "7 VMs" in out
    assert "20 vCPU" in out
    assert "38G RAM" in out


def test_main_rejects_an_invalid_config(configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        validate.main(["lab1"])


def test_main_names_a_missing_config(configs_dir):
    with pytest.raises(LabError, match="no config at .*lab1_cluster.yaml"):
        validate.main(["lab1"])


def test_main_prints_the_resolved_run(configs_dir, capsys):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    validate.main(["lab1", ""])
    assert "==> cluster_type   k8s" in capsys.readouterr().out
```

- [ ] **Step 3: Run both test files to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_install.py bamboo-specs/src/test/python/test_validate_prov.py -v`
Expected: FAIL — `TypeError: run() missing 1 required positional argument: 'addons'`

- [ ] **Step 4: Update install.py**

In `bamboo-specs/src/main/java/lab/provisioncluster/scripts/install.py`, change `USAGE`, `run`, and `main`:

```python
USAGE = "usage: install.py <cluster_name> [cluster_config]"
```

```python
def run(cluster: str, config):
    """Install everything the cluster asks for. Returns the component report path."""
    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster} — provision it first")

    technologies = config.enabled()
    secret_values = credentials.ensure(cluster, technologies)
```

leaving the rest of `run`'s body unchanged except the `extra_vars` call:

```python
        json.dump(
            extra_vars(config.cluster_type, technologies, report, secret_values), out
        )
```

and:

```python
def main(argv):
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    run(cluster, config)
```

`extra_vars` itself is unchanged — it still writes the `addons` comma string, which is what every `when:` clause in `site.yml` reads.

- [ ] **Step 5: Rewrite validate_prov.py**

Overwrite `bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py`:

```python
#!/usr/bin/env python3
"""Check the PROV plan variables and print what the run resolved to.

Runs as its own Bamboo stage, ahead of Provision and with no agent.role
requirement: it needs Python and the checkout, nothing else, so a bad config
fails in seconds on whatever agent is free rather than after a wait for the host
agent. provision.py repeats the same call, so a hand-run `make provision` gets
the identical checks and the identical message. The `_prov` suffix keeps this
importable alongside the DEPROV plan's validate_deprov.py under test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import planvars, proc  # noqa: E402

USAGE = "usage: validate_prov.py <cluster_name> [cluster_config]"

_HEAD = f"{'ROLE':<14} {'N':>3} {'CPU':>3} {'MEM':<5} {'DISK':<5}"


def _memory_gb(size: str) -> int:
    """A multipass size as whole gigabytes. 512M rounds down to 0, which is
    only ever a rounding artefact in a total nobody sizes a host from."""
    return int(size[:-1]) if size.endswith("G") else int(size[:-1]) // 1024


def report(cluster: str, config) -> list:
    """The resolved run, as lines. Pure — main prints them.

    The sizing roll-up is here rather than left to the apply because an
    over-sized cluster is cheap to catch now and expensive to catch when the
    host runs out of memory eight VMs in.
    """
    specs = config.roles()
    lines = [
        f"==> cluster_name   {cluster}",
        f"==> cluster_type   {config.cluster_type}",
        f"==> technologies   {','.join(config.enabled()) or 'none'}",
        f"==> config         {Path(config.source).name}",
        "",
        _HEAD,
    ]
    for spec in specs:
        lines.append(
            f"{spec.role:<14} {spec.count:>3} {spec.cpu:>3} "
            f"{spec.memory:<5} {spec.disk:<5}"
        )
    vms = sum(s.count for s in specs)
    cpus = sum(s.count * s.cpu for s in specs)
    memory = sum(s.count * _memory_gb(s.memory) for s in specs)
    lines += ["", f"==> total          {vms} VMs, {cpus} vCPU, {memory}G RAM"]
    return lines


def main(argv):
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    for line in report(cluster, config):
        print(line)


if __name__ == "__main__":
    proc.main(main)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_install.py bamboo-specs/src/test/python/test_validate_prov.py bamboo-specs/src/test/python/test_provision.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/install.py \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py \
        bamboo-specs/src/test/python/test_install.py \
        bamboo-specs/src/test/python/test_validate_prov.py
git commit -m "refactor: install and validate from the cluster config, with a sizing roll-up"
```

---

### Task 9: Deprovision without the config

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.py:9-32`
- Test: `bamboo-specs/src/test/python/test_deprovision.py`

**Interfaces:**
- Consumes: `terraform/.generated/<cluster>.tfvars.json` written by `provision.write_tfvars` (Task 7).
- Produces: `deprovision.destroy_args(cluster) -> list[str]` — `["-var-file=<path>"]` when the generated file exists, else `["-var", "nodes={}"]`.

- [ ] **Step 1: Update the test file**

In `bamboo-specs/src/test/python/test_deprovision.py`:

Delete the line `monkeypatch.setattr(deprovision.tfvars_mod, "resolve", lambda _: tmp_path / "x.tfvars")` and add, beside the other `paths` patches:

```python
    monkeypatch.setattr(deprovision.paths, "TF_DIR", tmp_path / "terraform")
```

Add `"tf_dir": tmp_path / "terraform",` to the returned `Lab` attributes.

Change `test_removes_the_generated_inventory_and_ssh_config` to write `[management]` instead of `[mgmt]`, and `test_sweeps_leftover_vms_after_terraform` to use `Node("lab1-management-1", [])` and `Node("other-management-1", [])`, asserting `("delete_purge", ("lab1-management-1",))`.

Add:

```python
def test_destroys_with_the_generated_var_file_when_it_exists(lab):
    generated = lab.tf_dir / ".generated"
    generated.mkdir(parents=True)
    varfile = generated / "lab1.tfvars.json"
    varfile.write_text('{"cluster_name": "lab1", "nodes": {}}\n')
    deprovision.main(["lab1"])
    destroy = next(c for c in lab.calls if c[0] == "destroy")
    assert f"-var-file={varfile}" in destroy


def test_destroys_with_an_empty_nodes_map_when_the_var_file_is_gone(lab):
    """State, not variables, decides what destroy removes — so a teardown never
    needs the cluster's config file, which may have been renamed or deleted."""
    deprovision.main(["lab1"])
    destroy = next(c for c in lab.calls if c[0] == "destroy")
    assert "nodes={}" in destroy


def test_removes_the_generated_var_file(lab):
    generated = lab.tf_dir / ".generated"
    generated.mkdir(parents=True)
    (generated / "lab1.tfvars.json").write_text('{"nodes": {}}\n')
    deprovision.main(["lab1"])
    assert not (generated / "lab1.tfvars.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_deprovision.py -v`
Expected: FAIL — `AttributeError: module 'deprovision' has no attribute 'tfvars_mod'` on the fixture, then the new assertions.

- [ ] **Step 3: Update deprovision.py**

In `bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.py`, delete the `from forgelab import tfvars as tfvars_mod` line. `from pathlib import Path` is already imported at the top for the `sys.path` bootstrap — `generated_tfvars` reuses it. Add both helpers below `USAGE`:

```python
def generated_tfvars(cluster: str) -> Path:
    return paths.TF_DIR / ".generated" / f"{cluster}.tfvars.json"


def destroy_args(cluster: str) -> list:
    """What to hand `terraform destroy` for its required `nodes` variable.

    provision.py leaves its generated var-file behind for exactly this. When it
    is missing — an older cluster, or a checkout that never provisioned this one
    — an empty map is enough: destroy works from state, not from variables.
    Deliberately not read from cluster_configs/, so renaming or deleting a
    config can never strand a running cluster.
    """
    varfile = generated_tfvars(cluster)
    if varfile.is_file():
        return [f"-var-file={varfile}"]
    return ["-var", "nodes={}"]
```

and in `main`, replace the `tfvars` lookup and the `destroy` call:

```python
    cluster = planvars.require_cluster_name(argv[0] if argv else "", USAGE)
    proc.require_tools("terraform", "multipass")

    # 1. Terraform destroy (if workspace exists)
    terraform.init()
    if terraform.workspace_select(cluster):
        terraform.destroy(
            *destroy_args(cluster), "-var", f"cluster_name={cluster}", "-input=false"
        )
        terraform.workspace_delete(cluster)
    else:
        print(f"no terraform workspace '{cluster}' — skipping destroy")
```

and in step 3 of `main`, beside the inventory removal:

```python
    (paths.INV_DIR / f"{cluster}.ini").unlink(missing_ok=True)
    generated_tfvars(cluster).unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_deprovision.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.py \
        bamboo-specs/src/test/python/test_deprovision.py
git commit -m "refactor: destroy from the generated var-file instead of the cluster config"
```

---

### Task 10: Rename the Ansible groups

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/site.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/tasks/main.yml:7,11,15,19`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/tasks/join.yml:3`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/dcos/tasks/main.yml:4`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/dcos/tasks/node.yml:4,8`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/dcos/templates/config.yaml.j2:2,7`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/tasks/main.yml:7,19,26,27`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/tasks/install.yml:129,136`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/handlers/main.yml:9,16`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/core-site.xml.j2:5`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/opensearch/tasks/main.yml:5,9,16,17`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/opensearch/templates/opensearch.yml.j2:11,15`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/tasks/main.yml:22`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/defaults/main.yml:3` (comment only)
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/templates/fluent-bit.conf.j2:15`

**Interfaces:**
- Consumes: the groups `inventory.render` now emits (Task 5) — `management`, `compute`, `hdfs_namenode`, `hdfs_datanode`, `opensearch_master`, plus the `k8s_nodes` / `hdfs_nodes` / `opensearch_nodes` children.
- Produces: nothing new. The `addons` extra-var and every `when:` clause are unchanged.

- [ ] **Step 1: Rewrite the two changed plays in site.yml**

In `bamboo-specs/src/main/java/lab/shared/ansible/site.yml`, change the Keycloak play's host line from `hosts: mgmt[0]` to:

```yaml
  hosts: management[0]
```

change the OpenSearch play's host line from `hosts: opensearch` to:

```yaml
  hosts: opensearch_nodes
```

and change the final "Record installed components" play's `hosts: mgmt[0]` to `hosts: management[0]`. Update its comment block to name the new groups:

```yaml
# Hand the component facts the roles set back to provision.py, which renders
# them into cluster_registered/<cluster>_cluster_info.yml. forgelab_components
# is set with set_fact, which is host-scoped, and roles run on different host
# groups (k8s on management+compute, keycloak on management[0], hdfs on
# hdfs_nodes, opensearch on opensearch_nodes) -- so no single host has the full
# picture. Aggregate across every host in the (naturally sorted, so stable)
# inventory before writing.
```

- [ ] **Step 2: Rename every group reference in the roles**

Run from the repo root:

```bash
ANSIBLE=bamboo-specs/src/main/java/lab/shared/ansible
grep -rlE "groups\['(mgmt|namenode|datanode|opensearch)'\]" "$ANSIBLE" \
  | xargs sed -i '' \
      -e "s/groups\['mgmt'\]/groups['management']/g" \
      -e "s/groups\['namenode'\]/groups['hdfs_namenode']/g" \
      -e "s/groups\['datanode'\]/groups['hdfs_datanode']/g" \
      -e "s/groups\['opensearch'\]/groups['opensearch_master']/g"
```

- [ ] **Step 3: Check nothing was missed**

Run:
```bash
grep -rnE "groups\['(mgmt|namenode|datanode|opensearch)'\]|hosts: (mgmt|opensearch)\b" \
  bamboo-specs/src/main/java/lab/shared/ansible
```
Expected: no output. If `roles/common/defaults/main.yml`'s prose comment still says `groups['opensearch'][0]`, the sed already fixed it; if any `hosts:` line remains, fix it by hand.

- [ ] **Step 4: Lint the playbooks**

Run: `cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint`
Expected: no errors. A dashed group name would warn here — there are none, every group uses underscores.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/
git commit -m "refactor: rename ansible groups to management and technology-prefixed nodes"
```

---

### Task 11: verify.py follows the renames

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py:56,72,84,87,115-116,234-235,247-285`
- Test: `bamboo-specs/src/test/python/test_verify.py:6,14`

**Interfaces:**
- Consumes: `inventory.control_ip` (Task 5) and the group names from Task 10.
- Produces: nothing new. `verify.py`'s argv contract — `<cluster> <cluster_type> [technologies]` — is unchanged.

- [ ] **Step 1: Update the test file**

In `bamboo-specs/src/test/python/test_verify.py`, change the two `lab1-mgmt-1` node names in the `kubectl get nodes` fixture strings to `lab1-management-1`.

- [ ] **Step 2: Run the tests to confirm they still pass**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -v`
Expected: PASS — these are string-parsing tests and the name is incidental, so this step is a rename, not a red bar.

- [ ] **Step 3: Rename in verify.py**

Run from the repo root:

```bash
sed -i '' \
  -e 's/mgmt_ip/control_ip/g' \
  bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py
```

Then fix by hand the three group lookups and the one message in `main`:

```python
    text = inv.read_text()
    control_ip = inventory.control_ip(text)
    if not control_ip:
        proc.die("no management host in inventory")
```

```python
    if "hdfs" in addons:
        namenode_ip = inventory.first_ip(text, "hdfs_namenode")
        if not namenode_ip:
            proc.die("hdfs is enabled but the inventory has no namenode host")
        datanodes = len(inventory.group_ips(text, "hdfs_datanode"))
        if not datanodes:
            proc.die("hdfs is enabled but the inventory has no datanode hosts")
        _verify_hdfs(namenode_ip, datanodes)
    if "opensearch" in addons:
        node_ip = inventory.first_ip(text, "opensearch_master")
        if not node_ip:
            proc.die("opensearch is enabled but the inventory has no opensearch hosts")
        _verify_opensearch(node_ip, len(inventory.group_ips(text, "opensearch_master")))
```

- [ ] **Step 4: Confirm no stale names remain**

Run: `grep -n "mgmt\|'namenode'\|'datanode'\|'opensearch'" bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py`
Expected: no output.

- [ ] **Step 5: Run the full Python suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS except `test_tfvars.py`, which Task 13 deletes.

- [ ] **Step 6: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py \
        bamboo-specs/src/test/python/test_verify.py
git commit -m "refactor: point verify at the renamed inventory groups"
```

---

### Task 12: The Bamboo plan's variables

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/ProvisionClusterSpec.java:26-30,40-49,68-73`
- Modify: `bamboo-specs/src/test/java/lab/provisioncluster/ProvisionClusterSpecTest.java`

**Interfaces:**
- Produces: PROV plan variables `cluster_name` (default `lab1`) and `cluster_config` (default empty). The `cluster_type` and `addons` variables and both `PLACEHOLDER_*` constants are gone.

- [ ] **Step 1: Update the spec test**

In `bamboo-specs/src/test/java/lab/provisioncluster/ProvisionClusterSpecTest.java`, delete the `planExposesTheAddonsVariable`, `variableDefaultsAreThePlaceholders`, and `placeholdersMatchPlanvars` tests along with the now-unused `Files`/`Path`/`List` imports, and add:

```java
    @Test
    public void planExposesTheNameAndConfigVariables() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertEquals("lab1", defaultOf(plan, "cluster_name"));
        assertEquals(
                "an empty cluster_config means the config named after the cluster",
                "",
                defaultOf(plan, "cluster_config"));
    }

    @Test
    public void planExposesNoOverrideVariables() {
        // cluster_type and addons now live in the cluster's YAML config, which
        // is the single source of truth: a run selects a config, it does not
        // patch one.
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertTrue(
                "no plan variable may shadow the config",
                plan.getVariables().stream()
                        .noneMatch(v -> "cluster_type".equals(v.getName())
                                || "addons".equals(v.getName())));
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `mvn -f bamboo-specs/pom.xml -q test`
Expected: FAIL — `AssertionError: no plan variable cluster_config`

- [ ] **Step 3: Update the spec**

In `bamboo-specs/src/main/java/lab/provisioncluster/ProvisionClusterSpec.java`, delete both `PLACEHOLDER_*` constants and the comment above them, and replace the `.variables(...)` call and its comment:

```java
                // A cluster's type, sizing and technologies live in
                // cluster_configs/<name>_cluster.yaml, so the only thing a run
                // chooses is which cluster to build and which config to build
                // it from. An empty cluster_config means the config named after
                // the cluster, which is what a run that only fills in
                // cluster_name should get.
                .variables(
                        new Variable("cluster_name", "lab1"),
                        new Variable("cluster_config", ""))
```

Replace both `ScriptTask` inline bodies so they pass the two variables:

```java
                                        new ScriptTask().description("validate plan variables")
                                                .inlineBody("bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py "
                                                        + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_config}\""))),
```

```java
                                new ScriptTask().description("provision cluster")
                                        .inlineBody("bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py "
                                                + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_config}\""))));
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `mvn -f bamboo-specs/pom.xml -q test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/ProvisionClusterSpec.java \
        bamboo-specs/src/test/java/lab/provisioncluster/ProvisionClusterSpecTest.java
git commit -m "refactor: replace the PROV override variables with cluster_config"
```

---

### Task 13: Cut over — add the config, delete the tfvars

**Files:**
- Create: `cluster_configs/lab1_cluster.yaml`
- Delete: `bamboo-specs/src/main/java/lab/shared/clusters/lab1.tfvars`
- Delete: `bamboo-specs/src/main/java/lab/shared/clusters/defaults.tfvars`
- Delete: `bamboo-specs/src/main/java/lab/shared/python/forgelab/tfvars.py`
- Delete: `bamboo-specs/src/test/python/test_tfvars.py`
- Modify: `Makefile:122-144`
- Modify: `bamboo-specs/src/test/python/test_sshconf.py:5,10,17`
- Modify: `bamboo-specs/src/test/python/test_multipass.py:8,10,18,30,35`
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/sshconf.py:17,83`

**Interfaces:**
- Consumes: everything from Tasks 1–12.
- Produces: `make provision CLUSTER=<name> [CONFIG=<name>]`, `make addons CLUSTER=<name> [CONFIG=<name>]`.

- [ ] **Step 1: Write the first config**

Create `cluster_configs/lab1_cluster.yaml`, reproducing today's `lab1.tfvars` under the new names:

```yaml
# The lab's default cluster. Provision it with:
#   make provision CLUSTER=lab1
# A technology with `enabled: false` keeps its sizing here, unvalidated, so it
# can be switched back on without retyping it.

cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G

  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      # Non-HA HDFS has exactly one NameNode, and it stores metadata only.
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G

      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G

  opensearch:
    enabled: true
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G

  keycloak:
    enabled: true
```

- [ ] **Step 2: Delete the tfvars layer**

```bash
git rm -r bamboo-specs/src/main/java/lab/shared/clusters
git rm bamboo-specs/src/main/java/lab/shared/python/forgelab/tfvars.py \
       bamboo-specs/src/test/python/test_tfvars.py
```

- [ ] **Step 3: Update the Makefile**

In `Makefile`, replace the `provision` and `addons` targets:

```make
provision: ## Provision cluster: make provision CLUSTER=lab1 [CONFIG=lab1]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/provisioncluster/scripts/provision.py $(CLUSTER) "$(CONFIG)"

.PHONY: addons
addons: ## Re-run the install stage only: make addons CLUSTER=lab1 [CONFIG=lab1]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/provisioncluster/scripts/install.py $(CLUSTER) "$(CONFIG)"
```

and delete this line from the `lint` target:

```make
	terraform -chdir=$(LAB)/shared/clusters fmt -check -recursive
```

- [ ] **Step 4: Rename in sshconf and its test**

In `bamboo-specs/src/main/java/lab/shared/python/forgelab/sshconf.py`, change the docstring's `ssh lab1-mgmt-1` to `ssh lab1-management-1`, and the printed hint from `f"ssh {cluster}-mgmt-1"` to:

```python
    print(f"==> ssh config: {conf} (try: ssh {cluster}-{CONTROL_ROLE}-1)")
```

adding `from .clusterconfig import CONTROL_ROLE` beside the existing `from . import paths`.

In `bamboo-specs/src/test/python/test_sshconf.py` and `bamboo-specs/src/test/python/test_multipass.py`, replace every `lab1-mgmt-1` with `lab1-management-1` and `other-mgmt-1` with `other-management-1`.

- [ ] **Step 5: Confirm no tfvars references survive**

Run: `grep -rn "tfvars\|CLUSTERS_DIR\|clusters_dir\|PLACEHOLDER_" --include="*.py" --include="*.java" --include="Makefile" bamboo-specs infra Makefile`
Expected: only `write_tfvars`, `generated_tfvars`, `destroy_args`' `.tfvars.json` strings, and their tests. No `tfvars_mod`, no `CLUSTERS_DIR`, no `clusters_dir` fixture, no `PLACEHOLDER_`.

- [ ] **Step 6: Run the full lint**

Run: `make lint`
Expected: pytest passes, `terraform fmt`/`validate` pass, `ansible-lint` passes, `mvn test` passes.

- [ ] **Step 7: Commit**

```bash
git add -A cluster_configs Makefile bamboo-specs
git commit -m "feat: cut over to cluster_configs and delete the tfvars layer"
```

---

### Task 14: Documentation

**Files:**
- Modify: `CLAUDE.md` (Commands, Layout map, Conventions)
- Modify: `README.md`
- Modify: `docs/provision-usage.md`
- Modify: `docs/using-cluster-addons.md`
- Modify: `bamboo-specs/src/main/java/lab/README.md`

**Interfaces:**
- Consumes: the finished behaviour from Tasks 1–13. Nothing consumes this task.

- [ ] **Step 1: Find every stale reference**

Run:
```bash
grep -rn "tfvars\|mgmt\|ADDONS=\|TYPE=\|addons plan variable\|cluster_type plan variable" \
  --include="*.md" . | grep -v docs/superpowers/plans | grep -v docs/superpowers/specs
```

Work through the hits file by file. `docs/superpowers/specs/` and `docs/superpowers/plans/` are historical records — leave them alone.

- [ ] **Step 2: Update CLAUDE.md**

In the **Commands** section, replace the `provision`/`deprovision` and `addons` bullets:

```markdown
- `make provision CLUSTER=lab1 [CONFIG=lab1]`
  / `make deprovision CLUSTER=lab1` — provision also writes
  `~/.forgelab/ssh_config.d/<cluster>.conf` (included from `~/.ssh/config`) so
  `ssh lab1-management-1` / `ssh <node-ip>` work as `ubuntu` with the lab key;
  deprovision removes it. `CONFIG=` picks which
  `cluster_configs/<name>_cluster.yaml` to build from; empty means the config
  named after the cluster. There is no fallback — a missing config is an error
  naming the path. Provision also writes
  `cluster_registered/<cluster>_cluster_info.yml` (tracked, uncommitted) as its
  last step; deprovision deletes it
- `make addons CLUSTER=lab1 [CONFIG=lab1]` — re-run the install stage only,
  against an existing cluster's inventory. Use it to iterate on an ansible role
  without a full rebuild
```

In **Layout map**, replace the `clusters/<name>.tfvars` clause in the `lab/shared/` bullet with `` `terraform/` (`modules/multipass/` = swappable VM backend boundary), `ansible/` `` alone, and add a new top-level bullet after `infra/`:

```markdown
- `cluster_configs/` — committed input, one `<name>_cluster.yaml` per cluster:
  its type, its node sizing, and which technologies it runs. Parsed and
  validated by `forgelab/clusterconfig.py`, which expands it into the Terraform
  `nodes` map, the ansible inventory groups, and the registry's sizing — so
  those three can no longer disagree. Repo-relative, unlike
  `cluster_registered/`: it is committed, so every Bamboo checkout carries it
```

In **Conventions**, replace the addons bullet, the VM-role bullet, and the plan-variables bullet:

```markdown
- Cluster technologies are opt-in per cluster: `technologies.<name>.enabled` in
  `cluster_configs/<cluster>_cluster.yaml`. A disabled technology keeps its
  sizing in the file, unvalidated, and contributes no VMs, no inventory groups
  and no ansible roles — enablement and sizing cannot disagree because they are
  the same block. k9s is not a technology — it ships with the k8s role. Secrets
  live in `~/.forgelab/<cluster>-credentials.yml` (0600), never in
  `cluster_registered/`
- A VM's role is its name: `<cluster>-<role>-<n>`. For `cluster_nodes` the role
  is the key verbatim (`management`, `compute`); for a technology's nodes it is
  `<technology>-<node>` (`hdfs-namenode`, `opensearch-master`), so two
  technologies can each own a `master`. The ansible group is the role with `-`
  replaced by `_`, because ansible warns on dashed group names. Rename a key in
  the config and the VM name, the group and the registry's `role` field all move
  together. hdfs owns two node roles — `namenode` (metadata only, no DataNode)
  and `datanode` — and `clusterconfig` requires exactly one NameNode, since
  non-HA HDFS has exactly one
- PROV takes two plan variables: `cluster_name` and `cluster_config` (empty
  means the config named after the cluster). Nothing overrides the config —
  it is the single source of truth, validated in `forgelab/clusterconfig.py`
  and nowhere else
- PROV and DEPROV each open with a `Validate` stage carrying NO `agent.role`
  requirement, so a bad config fails in seconds on any agent instead of queueing
  behind the host agent. Entrypoints call `planvars` first as well, so
  `make provision` fails identically
```

- [ ] **Step 3: Update README.md**

Replace the VM-tree fragment at `README.md:50` and its neighbours so the names match what gets built:

```
    ├── <name>-management-1..N
    ├── <name>-compute-1..N
    ├── <name>-hdfs-namenode-1
    ├── <name>-hdfs-datanode-1..N
    └── <name>-opensearch-master-1..N
```

Change the ssh example at `README.md:241` to `ssh <name>-management-1`, and the sample cluster-info block at `README.md:270-273` to `example: ssh lab1-management-1`, `- name: lab1-management-1`, `role: management`.

Replace the sizing list at `README.md:370` with a pointer to the config rather than a second copy of it:

```markdown
Sizing lives in `cluster_configs/<name>_cluster.yaml` — one block per role,
each with `count`, `cpu`, `memory` and `disk`. See `cluster_configs/lab1_cluster.yaml`
for the shipped default.
```

Add a section describing the config file, placed just before the provisioning walkthrough:

```markdown
## Cluster configs

A cluster is described by one file, `cluster_configs/<name>_cluster.yaml`:

```yaml
cluster:
  type: k8s                 # k8s | dcos

cluster_nodes:
  management:               # required — the control node
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:             # exactly one — HDFS here is non-HA
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G
  opensearch:
    enabled: false          # sizing below is kept, unvalidated, and builds nothing
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
  keycloak:
    enabled: true           # runs as pods on the cluster; declares no nodes
```

Every value is required when its block is enabled — there is no fallback file
and no defaulting, so reading this one file tells you exactly what you get.
`memory` and `disk` are multipass units (`4G`, `512M`), never `Gi`.

Build it with `make provision CLUSTER=lab1`, or point a differently-named
cluster at it with `make provision CLUSTER=lab2 CONFIG=lab1`.
```

- [ ] **Step 4: Update docs/provision-usage.md**

Replace the plan-variables table with:

| Variable | Default | Meaning |
| --- | --- | --- |
| `cluster_name` | `lab1` | Names every VM (`lab1-management-1`, …), the Terraform workspace, and the cluster info file |
| `cluster_config` | *(empty)* | Which `cluster_configs/<name>_cluster.yaml` to build from. Empty means the config named after the cluster |

Delete every paragraph describing the `cluster_type` and `addons` plan variables, the placeholder defaults, and the `none` keyword — none of them exist any more. Replace the "which addons produce which VMs" table with:

| Technology | VM roles | Count |
| --- | --- | --- |
| `hdfs` | `hdfs-namenode`, `hdfs-datanode` | as configured; exactly one NameNode |
| `opensearch` | `opensearch-master` | as configured |
| `keycloak` | *none* | 0 — it runs as pods on the k8s cluster the cluster nodes already form |

Update every `ssh lab1-mgmt-1` to `ssh lab1-management-1`, the expected `kubectl get nodes` output to `lab1-management-1`, `<mgmt-ip>` to `<management-ip>`, and the VM-count worked example so it counts the roles the shipped `lab1_cluster.yaml` declares (1 management + 2 compute + 1 hdfs-namenode + 3 hdfs-datanode + 3 opensearch-master = 10 VMs, of which 3 are k8s nodes).

Replace the troubleshooting entry at `docs/provision-usage.md:364` ("only mgmt and compute VMs exist") so its cause is the new one: a technology left `enabled: false` in the config, checked with `make provision`'s Validate stage roll-up rather than by reading a tfvars file.

Add a short section documenting the Validate stage's roll-up, since it is new user-visible output:

```markdown
### Checking a cluster's size before building it

The Validate stage prints the whole resolved run, including a per-role roll-up
and the totals:

```
==> cluster_name   lab1
==> cluster_type   k8s
==> technologies   hdfs,opensearch,keycloak
==> config         lab1_cluster.yaml

ROLE             N CPU MEM   DISK
management       1   2 4G    20G
compute          2   2 3G    20G
hdfs-namenode    1   2 4G    20G
hdfs-datanode    3   2 4G    40G
opensearch-master 3  2 6G    40G

==> total          10 VMs, 20 vCPU, 44G RAM
```

It runs on any agent and needs nothing but Python and the checkout, so a
mis-sized cluster costs seconds rather than a failed apply. `make provision`
performs the same checks before touching multipass.
```

- [ ] **Step 5: Update docs/using-cluster-addons.md**

Replace every `mgmt` host reference with `management`, every `lab1-mgmt-1` with `lab1-management-1`, and any instruction to edit a `.tfvars` file with the equivalent edit to `cluster_configs/<name>_cluster.yaml`. Where the document says an addon is enabled by the `addons` list, say instead that it is enabled by `technologies.<name>.enabled: true` in the cluster's config.

- [ ] **Step 6: Update lab/README.md**

Run: `grep -n "tfvars\|clusters/" bamboo-specs/src/main/java/lab/README.md`

Replace any `lab/shared/clusters/<name>.tfvars` mention in the shared-directory contract with `cluster_configs/<name>_cluster.yaml` at the repo root, noting that it is committed input read through `forgelab/clusterconfig.py` and is not under `lab/`.

- [ ] **Step 7: Confirm the docs are clean**

Run:
```bash
grep -rn "tfvars\|mgmt\|ADDONS=\|TYPE=" --include="*.md" . \
  | grep -v docs/superpowers/plans | grep -v docs/superpowers/specs
```
Expected: no output.

- [ ] **Step 8: Run the full lint one last time**

Run: `make lint`
Expected: all four checks pass.

- [ ] **Step 9: Commit**

```bash
git add CLAUDE.md README.md docs/provision-usage.md docs/using-cluster-addons.md \
        bamboo-specs/src/main/java/lab/README.md
git commit -m "docs: describe cluster_configs and the management role rename"
```

---

## Before the first real provision

`lab1` is running with `lab1-data-*` VMs that predate the namenode/datanode
split, so its Terraform state already diverges from `main`, and the role renames
change every key of the `nodes` map. Tear it down with the **pre-cutover**
checkout before merging, or purge it by hand afterwards:

```bash
multipass list | grep '^lab1-'
multipass delete --purge lab1-management-1 lab1-compute-1 ...   # every lab1- VM
multipass purge
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform workspace select default
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform workspace delete lab1
rm -f ~/.forgelab/ssh_config.d/lab1.conf cluster_registered/lab1_cluster_info.yml
```

Then `make provision CLUSTER=lab1` builds the cluster the new config describes.
