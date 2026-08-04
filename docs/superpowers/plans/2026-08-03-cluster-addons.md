# Cluster Addons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Provision plan an opt-in addon mechanism that installs Keycloak on the Kubernetes cluster and HDFS and Splunk on their own VMs, plus k9s and a working StorageClass on every k8s cluster.

**Architecture:** A comma-separated `addons` string in the cluster's tfvars (overridable by a Bamboo plan variable) is the single knob. `provision.py` derives Terraform VM counts from it and passes it to Ansible, where each addon is one role gated on the same variable. Every role reports what it installed into the existing `forgelab_components` fact, so the cluster info file builds itself. Verification is one function per addon, each a pure parser plus a retry loop.

**Tech Stack:** Python 3 standard library only, Terraform + the `larstobi/multipass` provider, Ansible (`ansible-lint` clean), Bamboo Java Specs, pytest.

## Global Constraints

- **Python is standard library only.** The host agent has no venv. Anything an entrypoint imports must ship with python3.
- **Parsing and rendering are pure functions** taking and returning strings; every external command goes through `proc.run` / `proc.run_out`. This is what makes the tests cheap.
- **Entrypoint filenames use underscores**, wrap `main(argv)` in `proc.main`, and take inputs as positional arguments. They must run from any working directory — put `shared/python` on `sys.path` via `Path(__file__).resolve().parents[2] / "shared" / "python"`.
- **Commits use Roj's git identity only.** No `Co-Authored-By`, no `Claude-Session`, no "Generated with Claude" footer, in commit messages or PR bodies.
- **Never commit** license keys, generated inventories, tfstate, or any password.
- **Multipass units are `"4G"` / `"20G"`**, never `Gi`.
- **Terraform applies keep `-parallelism=1`** (`terraform.apply_retry`). Concurrent `multipass launch` calls race in multipassd's MAC allocation and give every VM in the batch one shared DHCP lease. Do not drop the flag.
- **Ansible roles that install something** end `tasks/main.yml` by appending to the `forgelab_components` fact, with `# noqa: var-naming[no-role-prefix]` on the task. That is the only way a component reaches `cluster_registered/`.
- **Addon names are exactly** `keycloak`, `hdfs`, `splunk`. k9s is *not* an addon — it installs unconditionally with the `k8s` role.
- **VM sizing:** `data` = 2cpu/4G/40G ×3, `splunk` = 2cpu/6G/40G ×3. A fully loaded cluster is 40G of the host's 64G.
- **Run `make lint` before every commit.** It is pytest + `terraform fmt -check` + `terraform validate` + `ansible-lint` + `mvn test`.
- **Paths in this plan are repo-relative.** `$LAB` = `bamboo-specs/src/main/java/lab`.

---

### Task 1: Group-driven inventory rendering

`inventory.render()` hardcodes the mgmt and compute groups. The `data` and `splunk` VM roles need their own groups, and the Ansible plays need a `k8s_nodes` group so the k8s role stops targeting every host.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/inventory.py`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py:58-69`
- Test: `bamboo-specs/src/test/python/test_inventory.py`

**Interfaces:**
- Consumes: `forgelab.multipass.Node(name, ips)`, `forgelab.multipass.lan_ip(node)` — both already exist.
- Produces:
  - `inventory.render(cluster: str, groups: dict[str, list[Node]]) -> str`
  - `inventory.first_ip(text: str, group: str) -> str`
  - `inventory.group_ips(text: str, group: str) -> list[str]`
  - `inventory.mgmt_ip(text: str) -> str` (unchanged signature, now a wrapper)
  - `inventory.K8S_GROUPS: tuple[str, ...]` = `("mgmt", "compute")`

- [ ] **Step 1: Write the failing tests**

Replace the whole of `bamboo-specs/src/test/python/test_inventory.py` with:

```python
import pytest

from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

MGMT = [Node("lab1-mgmt-1", ["192.168.252.10", "10.244.0.1"])]
COMPUTE = [
    Node("lab1-compute-1", ["192.168.252.11"]),
    Node("lab1-compute-2", ["192.168.252.12"]),
]
DATA = [
    Node("lab1-data-1", ["192.168.252.21"]),
    Node("lab1-data-2", ["192.168.252.22"]),
]
SPLUNK = [Node("lab1-splunk-1", ["192.168.252.31"])]


def bare(cluster="lab1"):
    """A cluster with no addons: the data and splunk groups are empty."""
    return {"mgmt": MGMT, "compute": COMPUTE, "data": [], "splunk": []}


def loaded():
    return {"mgmt": MGMT, "compute": COMPUTE, "data": DATA, "splunk": SPLUNK}


def test_render_produces_the_expected_inventory():
    assert inventory.render("lab1", bare()) == (
        "[mgmt]\n"
        "lab1-mgmt-1 ansible_host=192.168.252.10\n"
        "\n"
        "[compute]\n"
        "lab1-compute-1 ansible_host=192.168.252.11\n"
        "lab1-compute-2 ansible_host=192.168.252.12\n"
        "\n"
        "[data]\n"
        "\n"
        "[splunk]\n"
        "\n"
        "[k8s_nodes:children]\n"
        "mgmt\n"
        "compute\n"
        "\n"
        "[all:vars]\n"
        "ansible_user=ubuntu\n"
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519\n"
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'\n"
        "cluster_name=lab1\n"
    )


def test_render_emits_empty_groups_so_plays_resolve_to_zero_hosts():
    """A `hosts: data` play must find an empty group, not an unknown one."""
    text = inventory.render("lab1", bare())
    assert "[data]\n" in text
    assert "[splunk]\n" in text


def test_render_uses_the_lan_address_not_the_pod_address():
    assert "10.244.0.1" not in inventory.render("lab1", bare())


def test_parse_hosts_reads_every_group():
    assert inventory.parse_hosts(inventory.render("lab1", loaded())) == [
        ("lab1-mgmt-1", "192.168.252.10"),
        ("lab1-compute-1", "192.168.252.11"),
        ("lab1-compute-2", "192.168.252.12"),
        ("lab1-data-1", "192.168.252.21"),
        ("lab1-data-2", "192.168.252.22"),
        ("lab1-splunk-1", "192.168.252.31"),
    ]


def test_parse_hosts_ignores_group_headers_and_vars():
    assert inventory.parse_hosts("[mgmt]\n\n[all:vars]\nansible_user=ubuntu\n") == []


def test_parse_hosts_ignores_the_children_group_members():
    """`mgmt` and `compute` under [k8s_nodes:children] are names, not hosts."""
    hosts = inventory.parse_hosts(inventory.render("lab1", bare()))
    assert ("mgmt", "") not in hosts
    assert len(hosts) == 3


def test_find_duplicate_ips_reports_the_mac_race():
    text = (
        "[mgmt]\na ansible_host=1.2.3.4\n"
        "[compute]\nb ansible_host=1.2.3.4\nc ansible_host=1.2.3.5\n"
    )
    assert inventory.find_duplicate_ips(text) == ["1.2.3.4"]


def test_find_duplicate_ips_is_empty_for_a_healthy_cluster():
    assert inventory.find_duplicate_ips(inventory.render("lab1", loaded())) == []


def test_first_ip_returns_the_first_host_of_the_named_group():
    text = inventory.render("lab1", loaded())
    assert inventory.first_ip(text, "data") == "192.168.252.21"
    assert inventory.first_ip(text, "splunk") == "192.168.252.31"


def test_first_ip_is_empty_for_an_empty_or_absent_group():
    text = inventory.render("lab1", bare())
    assert inventory.first_ip(text, "data") == ""
    assert inventory.first_ip(text, "nosuchgroup") == ""


def test_group_ips_returns_every_host_of_the_group_in_order():
    assert inventory.group_ips(inventory.render("lab1", loaded()), "data") == [
        "192.168.252.21",
        "192.168.252.22",
    ]


def test_group_ips_is_empty_for_an_empty_group():
    assert inventory.group_ips(inventory.render("lab1", bare()), "splunk") == []


def test_mgmt_ip_returns_the_first_mgmt_host():
    assert inventory.mgmt_ip(inventory.render("lab1", bare())) == "192.168.252.10"


def test_mgmt_ip_does_not_leak_a_compute_host():
    text = "[mgmt]\n\n[compute]\nc1 ansible_host=1.2.3.4\n"
    assert inventory.mgmt_ip(text) == ""


def test_assert_unique_ips_passes_on_a_healthy_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text(inventory.render("lab1", loaded()))
    inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_an_empty_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[mgmt]\n\n[compute]\n")
    with pytest.raises(LabError, match="has no hosts"):
        inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_duplicates(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[mgmt]\na ansible_host=1.2.3.4\nb ansible_host=1.2.3.4\n")
    with pytest.raises(LabError, match=r"duplicate node IP\(s\) \[1.2.3.4\]"):
        inventory.assert_unique_ips(inv)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_inventory.py -v`
Expected: FAIL — `render()` takes 3 positional arguments, `first_ip` and `group_ips` do not exist.

- [ ] **Step 3: Rewrite `render`, add `first_ip` / `group_ips`**

In `$LAB/shared/python/forgelab/inventory.py`, replace `render` and `mgmt_ip` with:

```python
# The groups the k8s and dcos roles target. Everything else in the inventory is
# a VM that must never receive kubelet.
K8S_GROUPS = ("mgmt", "compute")


def render(cluster: str, groups) -> str:
    """Build the .ini for a cluster from an ordered {group: [Node]} mapping.

    Empty groups are still emitted: a `hosts: data` play must resolve to zero
    hosts rather than fail on a group ansible has never heard of.
    """
    lines = []
    for group, nodes in groups.items():
        lines.append(f"[{group}]")
        lines += [f"{n.name} ansible_host={multipass.lan_ip(n)}" for n in nodes]
        lines.append("")
    lines += ["[k8s_nodes:children]", *K8S_GROUPS, ""]
    lines += [
        "[all:vars]",
        "ansible_user=ubuntu",
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'",
        f"cluster_name={cluster}",
    ]
    return "\n".join(lines) + "\n"


def group_ips(text: str, group: str) -> list:
    """Every host address in `group`, in file order. [] when empty or absent."""
    ips = []
    in_group = False
    for line in text.splitlines():
        if line.startswith("["):
            in_group = line.strip() == f"[{group}]"
            continue
        if in_group and "ansible_host=" in line:
            ips.append(line.split("ansible_host=", 1)[1].split()[0])
    return ips


def first_ip(text: str, group: str) -> str:
    """The first host address in `group`, or "" when the group is empty."""
    ips = group_ips(text, group)
    return ips[0] if ips else ""


def mgmt_ip(text: str) -> str:
    """The first mgmt-group host address, or "" when the group is empty."""
    return first_ip(text, "mgmt")
```

`parse_hosts` needs no change: `_HOST_RE` requires `ansible_host=`, so the bare
`mgmt` / `compute` lines under `[k8s_nodes:children]` are skipped already.

- [ ] **Step 4: Update the provision.py call site**

In `$LAB/provisioncluster/scripts/provision.py`, replace the `inv.write_text(...)` block with:

```python
    inv.write_text(
        inventory.render(
            cluster,
            {
                "mgmt": multipass.list_vms(f"{cluster}-mgmt-"),
                "compute": multipass.list_vms(f"{cluster}-compute-"),
                "data": multipass.list_vms(f"{cluster}-data-"),
                "splunk": multipass.list_vms(f"{cluster}-splunk-"),
            },
        )
    )
```

- [ ] **Step 5: Run the full test suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS. `test_provision.py` still passes — its fake backend returns only mgmt and compute VMs, and the two new groups render empty.

- [ ] **Step 6: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/inventory.py \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_inventory.py
git commit -m "refactor: group-driven inventory rendering with a k8s_nodes group"
```

---

### Task 2: The addons knob — parsing, resolution, validation

`addons` is a comma-separated scalar, not an HCL list, precisely so the existing flat-scalar parser needs no list support. Resolution mirrors `cluster_type`: the Bamboo plan variable wins when non-empty, otherwise the tfvars file.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/tfvars.py`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py`
- Test: `bamboo-specs/src/test/python/test_tfvars.py` (exists — **append**, never overwrite: it holds 8 passing tests for `resolve` and `parse_cluster_type`)
- Test: `bamboo-specs/src/test/python/test_provision.py`

**Interfaces:**
- Consumes: `tfvars.parse(text) -> dict` (exists, strips quotes).
- Produces:
  - `tfvars.parse_addons(text: str) -> list[str]`
  - `provision.ADDONS: tuple[str, ...]` = `("keycloak", "hdfs", "splunk")`
  - `provision.resolve_addons(override: str, tfvars_text: str, source: str) -> list[str]`

- [ ] **Step 1: Write the failing tfvars tests**

`bamboo-specs/src/test/python/test_tfvars.py` already exists and holds 8 passing
tests. **Append** these to it; do not overwrite the file. It already imports
`tfvars as tfvars_mod` — use that name rather than adding a second import.

```python
def test_parse_addons_splits_the_comma_list():
    text = 'addons = "keycloak,hdfs,splunk"\n'
    assert tfvars_mod.parse_addons(text) == ["keycloak", "hdfs", "splunk"]


def test_parse_addons_tolerates_spaces_and_trailing_commas():
    text = 'addons = "keycloak, hdfs ,"\n'
    assert tfvars_mod.parse_addons(text) == ["keycloak", "hdfs"]


def test_parse_addons_is_empty_when_the_value_is_empty():
    assert tfvars_mod.parse_addons('addons = ""\n') == []


def test_parse_addons_is_empty_when_the_key_is_absent():
    assert tfvars_mod.parse_addons('cluster_type = "k8s"\n') == []


def test_parse_addons_ignores_a_commented_line():
    assert tfvars_mod.parse_addons('# addons = "splunk"\n') == []


def test_parse_still_reads_the_new_sizing_scalars():
    text = 'addons = "hdfs"\ndata_mem = "4G"\ndata_count = 3\n'
    parsed = tfvars_mod.parse(text)
    assert parsed["data_mem"] == "4G"
    assert parsed["data_count"] == "3"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_tfvars.py -v`
Expected: FAIL with `AttributeError: module 'forgelab.tfvars' has no attribute 'parse_addons'`.

- [ ] **Step 3: Add `parse_addons`**

Append to `$LAB/shared/python/forgelab/tfvars.py`:

```python
def parse_addons(text: str) -> list:
    """The `addons = "a,b"` list, or [] when the key is absent or empty.

    A comma string rather than an HCL list: `parse` only handles flat scalars,
    and this keeps the cluster's settings in one file without teaching it more.
    """
    raw = parse(text).get("addons", "")
    return [name for name in (part.strip() for part in raw.split(",")) if name]
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest bamboo-specs/src/test/python/test_tfvars.py -v`
Expected: PASS — 14 tests (8 pre-existing plus the 6 added).

- [ ] **Step 5: Write the failing provision tests**

Append to `bamboo-specs/src/test/python/test_provision.py`:

```python
def test_resolve_addons_reads_the_tfvars_file():
    assert provision.resolve_addons("", 'addons = "hdfs,splunk"\n', "f.tfvars") == [
        "hdfs",
        "splunk",
    ]


def test_resolve_addons_prefers_the_override():
    assert provision.resolve_addons("keycloak", 'addons = "hdfs"\n', "f.tfvars") == [
        "keycloak"
    ]


def test_resolve_addons_falls_back_when_the_override_is_blank():
    """Bamboo always passes ${bamboo.addons}, which may be empty."""
    assert provision.resolve_addons("  ", 'addons = "hdfs"\n', "f.tfvars") == ["hdfs"]


def test_resolve_addons_rejects_an_unknown_name_from_the_file():
    with pytest.raises(LabError, match=r"unknown addon\(s\) \[kafka\] from f.tfvars"):
        provision.resolve_addons("", 'addons = "kafka"\n', "f.tfvars")


def test_resolve_addons_rejects_an_unknown_name_from_the_override():
    with pytest.raises(LabError, match="from the ADDONS override"):
        provision.resolve_addons("kafka", "", "f.tfvars")


def test_resolve_addons_names_the_known_addons_in_the_error():
    with pytest.raises(LabError, match="known: keycloak hdfs splunk"):
        provision.resolve_addons("kafka", "", "f.tfvars")


def test_passes_the_resolved_addons_to_ansible(lab, monkeypatch):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs"\n')
    recorded = []
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    provision.main(["lab1"])
    ansible = next(c for c in recorded if c[0] == "ansible-playbook")
    assert "addons=hdfs" in ansible


def test_addons_argument_overrides_the_tfvars_file(lab, monkeypatch):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs"\n')
    recorded = []
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    provision.main(["lab1", "", "keycloak"])
    ansible = next(c for c in recorded if c[0] == "ansible-playbook")
    assert "addons=keycloak" in ansible
```

- [ ] **Step 6: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_provision.py -v`
Expected: FAIL — `resolve_addons` does not exist.

- [ ] **Step 7: Add addon resolution to provision.py**

In `$LAB/provisioncluster/scripts/provision.py`, add below `CLUSTER_TYPES`:

```python
# k9s is deliberately absent: it is a kubectl TUI, installed unconditionally by
# the k8s role, not something a cluster opts into.
ADDONS = ("keycloak", "hdfs", "splunk")


def resolve_addons(override: str, tfvars_text: str, source: str) -> list:
    """The cluster's addon list. The plan variable wins over the tfvars file."""
    if override.strip():
        names = [n for n in (p.strip() for p in override.split(",")) if n]
        source = "the ADDONS override"
    else:
        names = tfvars_mod.parse_addons(tfvars_text)
    unknown = sorted({n for n in names if n not in ADDONS})
    if unknown:
        proc.die(
            f"unknown addon(s) [{' '.join(unknown)}] from {source}; "
            f"known: {' '.join(ADDONS)}"
        )
    return names
```

In `main`, read the third argument and resolve after `cluster_type` is settled:

```python
    type_override = argv[1] if len(argv) > 1 else ""
    addons_override = argv[2] if len(argv) > 2 else ""
```

and after the `cluster_type` validation block:

```python
    addons = resolve_addons(addons_override, tfvars.read_text(), str(tfvars))
    print(
        f"==> provisioning '{cluster}' type={cluster_type} "
        f"addons={','.join(addons) or 'none'} config={tfvars.name}"
    )
```

(replacing the existing `print`), and add `"-e", f"addons={','.join(addons)}",` to the
`ansible-playbook` argument list.

Update the usage string to `usage: provision.py <cluster_name> [cluster_type] [addons]`.

- [ ] **Step 8: Run the full suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/tfvars.py \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_tfvars.py \
        bamboo-specs/src/test/python/test_provision.py
git commit -m "feat: resolve and validate the per-cluster addons list"
```

---

### Task 3: Terraform `data` and `splunk` VM roles

Node counts live in tfvars, but the addon list decides whether a role exists at all. `provision.py` only ever turns a role *off*, on the terraform command line where `-var` beats `-var-file`. One source of truth, no two settings to disagree.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/terraform/variables.tf`
- Modify: `bamboo-specs/src/main/java/lab/shared/terraform/main.tf`
- Modify: `bamboo-specs/src/main/java/lab/shared/clusters/defaults.tfvars`
- Modify: `bamboo-specs/src/main/java/lab/shared/clusters/lab1.tfvars`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py`
- Test: `bamboo-specs/src/test/python/test_provision.py`

**Interfaces:**
- Consumes: `provision.resolve_addons` from Task 2.
- Produces: `provision.node_count_overrides(addons) -> list[str]` — the `-var <role>_count=0` arguments for roles whose addon is off. `provision.ADDON_NODE_ROLES: dict[str, str]` = `{"hdfs": "data", "splunk": "splunk"}`.

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_provision.py`:

```python
def test_node_count_overrides_zeroes_every_role_when_no_addons():
    assert provision.node_count_overrides([]) == [
        "-var", "data_count=0", "-var", "splunk_count=0",
    ]


def test_node_count_overrides_leaves_an_enabled_role_alone():
    assert provision.node_count_overrides(["hdfs"]) == ["-var", "splunk_count=0"]


def test_node_count_overrides_is_empty_when_every_role_is_wanted():
    assert provision.node_count_overrides(["hdfs", "splunk", "keycloak"]) == []


def test_keycloak_alone_builds_no_extra_vms():
    """Keycloak runs on the k8s cluster; it needs no VM role of its own."""
    assert provision.node_count_overrides(["keycloak"]) == [
        "-var", "data_count=0", "-var", "splunk_count=0",
    ]


def test_apply_zeroes_the_vm_roles_of_disabled_addons(lab):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = ""\n')
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert "data_count=0" in apply
    assert "splunk_count=0" in apply


def test_apply_keeps_the_vm_roles_of_enabled_addons(lab):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs,splunk"\n')
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert "data_count=0" not in apply
    assert "splunk_count=0" not in apply
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_provision.py -k node_count -v`
Expected: FAIL — `node_count_overrides` does not exist.

- [ ] **Step 3: Add the override helper and wire it into the apply**

In `$LAB/provisioncluster/scripts/provision.py`, below `ADDONS`:

```python
# Which addon owns which VM role. Keycloak owns none — it runs on the k8s
# cluster the mgmt/compute nodes already form.
ADDON_NODE_ROLES = {"hdfs": "data", "splunk": "splunk"}


def node_count_overrides(addons) -> list:
    """`-var <role>_count=0` for every VM role whose addon is off.

    Counts are configured in the cluster's tfvars and only ever turned *off*
    here, so the addon list and the sizing file cannot disagree. `-var` beats
    `-var-file` on the terraform command line, which is what makes this work.
    """
    args = []
    for addon, role in ADDON_NODE_ROLES.items():
        if addon not in addons:
            args += ["-var", f"{role}_count=0"]
    return args
```

Change the apply call to:

```python
    terraform.apply_retry(
        f"-var-file={tfvars}",
        "-var", f"cluster_name={cluster}",
        *node_count_overrides(addons),
        "-input=false",
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS.

- [ ] **Step 5: Add the Terraform variables**

Append to `$LAB/shared/terraform/variables.tf`:

```hcl
variable "addons" {
  type        = string
  default     = ""
  description = "Comma-separated addon names. provision.py derives node counts from it; terraform only records it."
}
variable "data_count" {
  type    = number
  default = 0
}
variable "data_cpu" {
  type    = number
  default = 2
}
variable "data_mem" {
  type    = string
  default = "4G"
}
variable "data_disk" {
  type    = string
  default = "40G"
}
variable "splunk_count" {
  type    = number
  default = 0
}
variable "splunk_cpu" {
  type    = number
  default = 2
}
variable "splunk_mem" {
  type    = string
  default = "6G"
}
variable "splunk_disk" {
  type    = string
  default = "40G"
}
```

The counts default to 0 so a plain `terraform apply` with no var file builds only
the k8s nodes, exactly as it does today. `addons` is declared but unused by
Terraform — without the declaration, every apply that passes the cluster's var
file warns about an undeclared variable.

- [ ] **Step 6: Add the node locals**

In `$LAB/shared/terraform/main.tf`, add to `locals`:

```hcl
  data_nodes = {
    for i in range(var.data_count) :
    "${var.cluster_name}-data-${i + 1}" => {
      cpus = var.data_cpu, memory = var.data_mem, disk = var.data_disk
    }
  }
  splunk_nodes = {
    for i in range(var.splunk_count) :
    "${var.cluster_name}-splunk-${i + 1}" => {
      cpus = var.splunk_cpu, memory = var.splunk_mem, disk = var.splunk_disk
    }
  }
```

and change the module's `nodes`:

```hcl
  nodes = merge(
    local.mgmt_nodes,
    local.compute_nodes,
    local.data_nodes,
    local.splunk_nodes,
  )
```

The `<cluster>-<role>-<n>` naming is load-bearing: `registry.nodes_from` derives a
node's role from `name.split("-")[-2]` and uses it to look up `<role>_cpu` /
`_mem` / `_disk` in the tfvars. Both new roles get their sizing for free.

- [ ] **Step 7: Update the cluster settings files**

`$LAB/shared/clusters/defaults.tfvars` — append (a default cluster stays bare and cheap):

```hcl
addons        = ""
data_count    = 3
data_cpu      = 2
data_mem      = "4G"
data_disk     = "40G"
splunk_count  = 3
splunk_cpu    = 2
splunk_mem    = "6G"
splunk_disk   = "40G"
```

`$LAB/shared/clusters/lab1.tfvars` — append the same block but with:

```hcl
addons        = "keycloak,hdfs,splunk"
```

- [ ] **Step 8: Validate the Terraform**

```bash
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform fmt -recursive
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform fmt -check -recursive
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform init -backend=false -input=false
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 9: Confirm the plan builds six extra VMs**

```bash
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform plan \
  -var-file=$PWD/bamboo-specs/src/main/java/lab/shared/clusters/lab1.tfvars \
  -var cluster_name=lab1 -input=false | grep -c 'multipass_instance.node\["lab1-'
```
Expected: 9 (1 mgmt + 2 compute + 3 data + 3 splunk).

- [ ] **Step 10: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/terraform/variables.tf \
        bamboo-specs/src/main/java/lab/shared/terraform/main.tf \
        bamboo-specs/src/main/java/lab/shared/clusters/defaults.tfvars \
        bamboo-specs/src/main/java/lab/shared/clusters/lab1.tfvars \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_provision.py
git commit -m "feat: data and splunk VM roles, sized in tfvars and gated by addons"
```

---

### Task 4: Per-cluster credentials

Keycloak and Splunk need admin passwords. `cluster_registered/` is tracked, so it gets a pointer and nothing else; the secrets go to `~/.forgelab/<cluster>-credentials.yml` at mode 0600, outside the repository entirely.

**Files:**
- Create: `bamboo-specs/src/main/java/lab/shared/python/forgelab/credentials.py`
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/registry.py`
- Modify: `bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.py`
- Test: `bamboo-specs/src/test/python/test_credentials.py` (create)
- Test: `bamboo-specs/src/test/python/test_registry.py`
- Test: `bamboo-specs/src/test/python/test_deprovision.py`

**Interfaces:**
- Produces:
  - `credentials.SECRET_KEYS: dict[str, tuple[str, ...]]`
  - `credentials.path(cluster: str) -> Path`
  - `credentials.generate(addons) -> dict[str, str]`
  - `credentials.render(cluster: str, values: dict) -> str`
  - `credentials.write(cluster: str, values: dict) -> Path`
  - `credentials.read(cluster: str) -> dict[str, str]`
  - `credentials.remove(cluster: str) -> None`
  - `registry.render(..., credentials="")` and `registry.write(..., credentials="")` — a new
    trailing keyword argument holding the credentials file path.
- Key names produced here and consumed by Tasks 9 and 13:
  `keycloak_admin_password`, `keycloak_app_user_password`, `splunk_admin_password`.

- [ ] **Step 1: Write the failing tests**

Create `bamboo-specs/src/test/python/test_credentials.py`:

```python
import stat

import pytest

from forgelab import credentials, registry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.paths, "FORGELAB_HOME", tmp_path)
    return tmp_path


def test_generate_covers_only_the_enabled_addons():
    assert sorted(credentials.generate(["splunk"])) == ["splunk_admin_password"]


def test_generate_covers_keycloak_admin_and_app_user():
    assert sorted(credentials.generate(["keycloak"])) == [
        "keycloak_admin_password",
        "keycloak_app_user_password",
    ]


def test_generate_is_empty_for_an_addon_with_no_secrets():
    assert credentials.generate(["hdfs"]) == {}


def test_generate_is_empty_for_no_addons():
    assert credentials.generate([]) == {}


def test_generate_never_repeats_a_password():
    values = credentials.generate(["keycloak", "splunk"])
    assert len(set(values.values())) == 3


def test_generate_passwords_are_long_enough_for_splunk():
    """Splunk refuses an admin password shorter than 8 characters."""
    values = credentials.generate(["splunk"])
    assert len(values["splunk_admin_password"]) >= 8


def test_render_quotes_values_and_sorts_keys():
    text = credentials.render("lab1", {"b_password": "two", "a_password": "one"})
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines == ['a_password: "one"', 'b_password: "two"']


def test_render_names_the_cluster_in_the_header():
    assert "lab1" in credentials.render("lab1", {})


def test_write_is_owner_only(home):
    creds = credentials.write("lab1", {"splunk_admin_password": "hunter22"})
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600


def test_write_lands_beside_the_ssh_key_not_in_the_repo(home):
    assert credentials.write("lab1", {}).parent == home


def test_read_round_trips_what_write_wrote(home):
    values = {"splunk_admin_password": "hunter22", "keycloak_admin_password": "abc-_1"}
    credentials.write("lab1", values)
    assert credentials.read("lab1") == values


def test_read_is_empty_when_there_is_no_file(home):
    assert credentials.read("nosuch") == {}


def test_remove_deletes_the_file(home):
    credentials.write("lab1", {"splunk_admin_password": "hunter22"})
    credentials.remove("lab1")
    assert not credentials.path("lab1").exists()


def test_remove_is_quiet_when_there_is_no_file(home):
    credentials.remove("nosuch")


def test_registry_records_the_pointer_not_the_secret(home, monkeypatch):
    monkeypatch.setattr(registry.paths, "SSH_KEY", home / "id_ed25519")
    text = registry.render(
        "lab1", "k8s", "2026-08-03T10:00:00Z", [], [],
        credentials=home / "lab1-credentials.yml",
    )
    assert "lab1-credentials.yml" in text
    assert "hunter22" not in text
    assert "password" not in text


def test_registry_omits_the_pointer_when_there_are_no_credentials(home, monkeypatch):
    monkeypatch.setattr(registry.paths, "SSH_KEY", home / "id_ed25519")
    text = registry.render("lab1", "k8s", "2026-08-03T10:00:00Z", [], [])
    assert "credentials:" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_credentials.py -v`
Expected: FAIL with `ImportError: cannot import name 'credentials'`.

- [ ] **Step 3: Write the module**

Create `$LAB/shared/python/forgelab/credentials.py`:

```python
"""Per-cluster secrets, at ~/.forgelab/<cluster>-credentials.yml.

Deliberately outside the repository: `cluster_registered/` is tracked, so the
cluster's info file carries a pointer to this path and never a password. Same
shape as registry.py — pure render, then write — and standard library only.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from . import paths

# The generated secrets each addon needs. An addon absent from this map (hdfs)
# simply has none.
SECRET_KEYS = {
    "keycloak": ("keycloak_admin_password", "keycloak_app_user_password"),
    "splunk": ("splunk_admin_password",),
}

# token_urlsafe(18) is 24 characters of [A-Za-z0-9_-]: comfortably past Splunk's
# 8-character floor, and safe inside a double-quoted YAML scalar unescaped.
PASSWORD_BYTES = 18


def path(cluster: str) -> Path:
    return paths.FORGELAB_HOME / f"{cluster}-credentials.yml"


def generate(addons) -> dict:
    """A fresh password for every secret the enabled addons need."""
    return {
        key: secrets.token_urlsafe(PASSWORD_BYTES)
        for addon in addons
        for key in SECRET_KEYS.get(addon, ())
    }


def render(cluster: str, values: dict) -> str:
    """Build the credentials file. Pure — every value is passed in."""
    lines = [
        f"# Generated by lab/provisioncluster/scripts/install.py for '{cluster}'.",
        "# Never tracked: cluster_registered/ holds only a pointer to this file.",
    ]
    lines += [f'{key}: "{values[key]}"' for key in sorted(values)]
    return "\n".join(lines) + "\n"


def write(cluster: str, values: dict) -> Path:
    """Write the credentials file, owner-readable only. Returns its path."""
    paths.FORGELAB_HOME.mkdir(parents=True, exist_ok=True)
    creds = path(cluster)
    creds.write_text(render(cluster, values))
    creds.chmod(0o600)
    print(f"==> credentials: {creds}")
    return creds


def read(cluster: str) -> dict:
    """The cluster's secrets, or {} when the file is absent.

    verify.py needs the passwords to prove a login works; this is how it gets
    them without them ever appearing on a command line.
    """
    creds = path(cluster)
    if not creds.is_file():
        return {}
    values = {}
    for line in creds.read_text().splitlines():
        if line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        values[key.strip()] = raw.strip().strip('"')
    return values


def remove(cluster: str):
    path(cluster).unlink(missing_ok=True)
```

- [ ] **Step 4: Add the registry pointer**

In `$LAB/shared/python/forgelab/registry.py`, change both signatures and append the line:

```python
def render(cluster: str, cluster_type: str, provisioned_at: str, nodes, components,
           credentials="") -> str:
```

At the end of `render`, before the `return`:

```python
    # A pointer, never the secrets themselves — this file is tracked.
    if credentials:
        lines.append(f"credentials: {_scalar(_home_relative(credentials))}")
```

```python
def write(cluster: str, cluster_type: str, provisioned_at: str, nodes, components,
          credentials="") -> Path:
```

and pass `credentials` through to `render` inside `write`.

- [ ] **Step 5: Clean up on deprovision**

In `$LAB/deprovisioncluster/scripts/deprovision.py`, add `credentials` to the
`forgelab` import and, in step 3:

```python
    credentials.remove(cluster)
```

Append to `bamboo-specs/src/test/python/test_deprovision.py`:

```python
def test_removes_the_credentials_file(lab, tmp_path, monkeypatch):
    monkeypatch.setattr(deprovision.credentials.paths, "FORGELAB_HOME", tmp_path)
    deprovision.credentials.write("lab1", {"splunk_admin_password": "hunter22"})
    deprovision.main(["lab1"])
    assert not deprovision.credentials.path("lab1").exists()
```

- [ ] **Step 6: Run the full suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS. `test_registry.py` is unaffected — `credentials` defaults to `""`.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/credentials.py \
        bamboo-specs/src/main/java/lab/shared/python/forgelab/registry.py \
        bamboo-specs/src/main/java/lab/deprovisioncluster/scripts/deprovision.py \
        bamboo-specs/src/test/python/test_credentials.py \
        bamboo-specs/src/test/python/test_deprovision.py
git commit -m "feat: per-cluster credentials file, referenced but never inlined by the registry"
```

---

### Task 5: Extract the install stage into `install.py` and add `make addons`

Writing an Ansible role against a 30-minute rebuild loop is not workable. Splitting the install stage out gives a re-runnable entrypoint, and is also where the secrets get handed to Ansible through a file rather than argv.

**Files:**
- Create: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/install.py`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py`
- Modify: `Makefile`
- Test: `bamboo-specs/src/test/python/test_install.py` (create)

**Interfaces:**
- Consumes: `credentials.generate`, `credentials.write` (Task 4); `tfvars.parse_addons`, `tfvars.parse_cluster_type`, `tfvars.resolve`.
- Produces:
  - `install.extra_vars(cluster_type: str, addons, report: Path, secret_values: dict) -> dict`
  - `install.run(cluster: str, cluster_type: str, addons) -> Path` — returns the component report path.

- [ ] **Step 1: Write the failing tests**

Create `bamboo-specs/src/test/python/test_install.py`:

```python
import json
import stat
from pathlib import Path

import pytest

import install
from forgelab.proc import LabError


@pytest.fixture
def lab(tmp_path, monkeypatch):
    recorded = []
    inv_dir = tmp_path / "inventory"
    inv_dir.mkdir()
    (inv_dir / "lab1.ini").write_text("[mgmt]\nlab1-mgmt-1 ansible_host=1.2.3.4\n")

    monkeypatch.setattr(install.paths, "INV_DIR", inv_dir)
    monkeypatch.setattr(install.credentials.paths, "FORGELAB_HOME", tmp_path / "home")
    monkeypatch.setattr(
        install.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    return type("Lab", (), {"recorded": recorded, "inv_dir": inv_dir, "home": tmp_path})


def test_extra_vars_carries_the_playbook_inputs():
    payload = install.extra_vars("k8s", ["hdfs"], Path("/tmp/r.json"), {})
    assert payload["cluster_type"] == "k8s"
    assert payload["addons"] == "hdfs"
    assert payload["component_report"] == "/tmp/r.json"


def test_extra_vars_joins_an_empty_addon_list_to_an_empty_string():
    assert install.extra_vars("k8s", [], Path("/tmp/r.json"), {})["addons"] == ""


def test_extra_vars_merges_the_secrets_in():
    payload = install.extra_vars("k8s", ["splunk"], Path("/r"), {"splunk_admin_password": "x"})
    assert payload["splunk_admin_password"] == "x"


def test_run_refuses_a_cluster_with_no_inventory(lab):
    with pytest.raises(LabError, match="no inventory for nosuch"):
        install.run("nosuch", "k8s", [])


def test_run_invokes_the_playbook_with_the_inventory(lab):
    install.run("lab1", "k8s", [])
    call = lab.recorded[0]
    assert call[0] == "ansible-playbook"
    assert str(lab.inv_dir / "lab1.ini") in call


def test_run_passes_variables_by_file_never_on_the_command_line(lab):
    """argv is world-readable in `ps`; a password must never appear there."""
    install.run("lab1", "k8s", ["splunk"])
    call = lab.recorded[0]
    assert any(arg.startswith("@") for arg in call)
    assert not any("password" in arg for arg in call)


def test_run_writes_the_credentials_file_for_addons_that_need_one(lab):
    install.run("lab1", "k8s", ["splunk"])
    assert install.credentials.path("lab1").is_file()


def test_run_writes_no_credentials_file_when_nothing_needs_one(lab):
    install.run("lab1", "k8s", ["hdfs"])
    assert not install.credentials.path("lab1").exists()


def test_run_deletes_the_variables_file_afterwards(lab):
    install.run("lab1", "k8s", ["splunk"])
    varsfile = next(a[1:] for a in lab.recorded[0] if a.startswith("@"))
    assert not Path(varsfile).exists()


def test_the_variables_file_is_owner_only_while_it_exists(lab, monkeypatch):
    seen = {}

    def capture(*args, **kwargs):
        varsfile = Path(next(str(a)[1:] for a in args if str(a).startswith("@")))
        seen["mode"] = stat.S_IMODE(varsfile.stat().st_mode)
        seen["payload"] = json.loads(varsfile.read_text())

    monkeypatch.setattr(install.proc, "run", capture)
    install.run("lab1", "k8s", ["splunk"])
    assert seen["mode"] == 0o600
    assert seen["payload"]["splunk_admin_password"]


def test_run_returns_the_component_report_path(lab):
    report = install.run("lab1", "k8s", [])
    assert report.name == "components.json"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_install.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'install'`.

- [ ] **Step 3: Write install.py**

Create `$LAB/provisioncluster/scripts/install.py` (chmod +x it):

```python
#!/usr/bin/env python3
"""Run the install stage against an already-provisioned cluster's inventory.

Split out of provision.py so a role can be iterated on with `make addons`
instead of a full rebuild. provision.py imports `run` rather than shelling out,
so there is exactly one code path.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import credentials, paths, proc  # noqa: E402
from forgelab import tfvars as tfvars_mod  # noqa: E402


def extra_vars(cluster_type: str, addons, report, secret_values: dict) -> dict:
    """The -e payload for site.yml. Pure — the caller writes it out."""
    return {
        "cluster_type": cluster_type,
        "addons": ",".join(addons),
        "component_report": str(report),
        **secret_values,
    }


def run(cluster: str, cluster_type: str, addons):
    """Install everything the cluster asks for. Returns the component report path."""
    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster} — provision it first")

    secret_values = credentials.generate(addons)
    if secret_values:
        credentials.write(cluster, secret_values)

    # mkdtemp is 0700, and the vars file is opened 0600 and deleted after the
    # run: passwords must never reach argv, which is world-readable in `ps`.
    workdir = Path(tempfile.mkdtemp(prefix="forgelab-"))
    report = workdir / "components.json"
    varsfile = workdir / "extra-vars.json"
    handle = os.open(varsfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w") as out:
        json.dump(extra_vars(cluster_type, addons, report, secret_values), out)

    try:
        proc.run(
            "ansible-playbook",
            paths.SITE_YML,
            "-i", inv,
            "-e", f"@{varsfile}",
            env=paths.ansible_env(os.environ),
        )
    finally:
        varsfile.unlink(missing_ok=True)
    return report


def main(argv):
    cluster = argv[0] if argv else ""
    if not cluster:
        proc.die("usage: install.py <cluster_name> [cluster_type] [addons]")
    text = tfvars_mod.resolve(cluster).read_text()
    cluster_type = (argv[1] if len(argv) > 1 else "") or tfvars_mod.parse_cluster_type(text)
    override = argv[2] if len(argv) > 2 else ""
    addons = (
        [a for a in (p.strip() for p in override.split(",")) if a]
        if override.strip()
        else tfvars_mod.parse_addons(text)
    )
    run(cluster, cluster_type, addons)


if __name__ == "__main__":
    proc.main(main)
```

- [ ] **Step 4: Delegate from provision.py**

In `$LAB/provisioncluster/scripts/provision.py`: drop the `tempfile` import, add
`import install` below the `forgelab` imports, and replace all of Stage 3 with:

```python
    # Stage 3: Install. The roles report what they installed into this file.
    report = install.run(cluster, cluster_type, addons)
```

Stage 5 gains the credentials pointer:

```python
    registry.write(
        cluster,
        cluster_type,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        registry.nodes_from(
            inventory.parse_hosts(inv.read_text()), tfvars_mod.parse(tfvars.read_text())
        ),
        registry.read_components(report),
        credentials=(
            credentials.path(cluster) if credentials.path(cluster).is_file() else ""
        ),
    )
```

Add `credentials` to the `forgelab` import list, and drop `os` if nothing else uses it.

- [ ] **Step 5: Add the make target**

In the `Makefile`, after the `provision` target:

```make
.PHONY: addons
addons: ## Re-run the install stage only: make addons CLUSTER=lab1 [TYPE=] [ADDONS=]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/provisioncluster/scripts/install.py $(CLUSTER) "$(TYPE)" "$(ADDONS)"
```

and declare `ADDONS ?=` next to `TYPE ?=` at the top.

- [ ] **Step 6: Run the full suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS. `test_provision.py`'s existing assertions on the `ansible-playbook`
call now need `install.proc.run` stubbed, not `provision.proc.run` — update the two
tests that inspect it (`test_tells_ansible_where_to_report_components`,
`test_passes_the_resolved_cluster_type_to_ansible`, plus the two added in Task 2)
to monkeypatch `provision.install.proc.run` and to read the values out of the
`@varsfile` JSON instead of argv. Also update `test_runs_the_stages_in_order` and
`test_leaves_no_cluster_info_when_verify_fails`, which count `proc.run` calls.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/install.py \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_install.py \
        bamboo-specs/src/test/python/test_provision.py \
        Makefile
git commit -m "feat: re-runnable install stage with secrets passed by file, not argv"
```

---

### Task 6: Stop the k8s role targeting every VM

`site.yml` runs the k8s role on `hosts: all`. With `data` and `splunk` VMs in the inventory that installs kubelet on them. The kernel preparation in `roles/common` has the same problem.

**Deviation from the spec, and why:** the spec says the kernel prep "moves into the `k8s` role". DC/OS needs the identical preparation, so moving it there would break `cluster_type=dcos`. It stays in `common`, gated on membership in `k8s_nodes` — same outcome (data and splunk VMs are untouched), and both cluster types keep working.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/site.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/tasks/main.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/tasks/cluster_node.yml`

**Interfaces:**
- Consumes: the `k8s_nodes` inventory group from Task 1; the `addons` variable from Task 5's `extra_vars`.
- Produces: `site.yml` plays targeted at `k8s_nodes`, and three addon plays (bodies land in Tasks 9, 11, 13).

- [ ] **Step 1: Move the kernel preparation into its own tasks file**

Create `$LAB/shared/ansible/roles/common/tasks/cluster_node.yml` holding the four
tasks currently at the top of `main.yml` — `Disable swap now`, `Load kernel modules
for container networking`, `Persist kernel modules`, `Sysctl for bridged/forwarded
traffic` — copied verbatim, with `---` as the first line.

- [ ] **Step 2: Rewrite `roles/common/tasks/main.yml`**

```yaml
---
- name: Base packages
  ansible.builtin.apt:
    name: [curl, gnupg, ca-certificates, apt-transport-https]
    state: present
    update_cache: true

# Swap, bridge modules and sysctls are cluster-node preparation. The data and
# splunk VMs are not cluster nodes and must not be touched by it — HDFS in
# particular has no reason to run with swap off.
- name: Cluster-node kernel preparation
  ansible.builtin.include_tasks: cluster_node.yml
  when: inventory_hostname in groups['k8s_nodes'] | default([])
```

- [ ] **Step 3: Retarget the plays in `site.yml`**

Change the Kubernetes play and the DC/OS play from `hosts: all` to
`hosts: k8s_nodes`. Leave the `common` play on `hosts: all` and the component
report play on `hosts: mgmt[0]`.

- [ ] **Step 4: Add the three addon plays**

Between the DC/OS play and the component report play in `site.yml`:

```yaml
- name: Keycloak on the cluster
  hosts: mgmt[0]
  become: true
  roles:
    - role: keycloak
      when: "'keycloak' in addons.split(',')"

- name: HDFS on the data nodes
  hosts: data
  become: true
  roles:
    - role: hdfs
      when: "'hdfs' in addons.split(',')"

- name: Splunk on the splunk nodes
  hosts: splunk
  become: true
  roles:
    - role: splunk
      when: "'splunk' in addons.split(',')"
```

`addons.split(',')` rather than `'keycloak' in addons`: the latter is a substring
test on a string and would match a future addon whose name contains another's.

The three roles do not exist yet. Create the minimum that lints and does nothing,
so this task's syntax check passes and Tasks 9, 11 and 13 fill them in:

```bash
for role in keycloak hdfs splunk; do
  mkdir -p bamboo-specs/src/main/java/lab/shared/ansible/roles/$role/{tasks,defaults}
  printf -- '---\n# Filled in by the %s task of the cluster-addons plan.\n' "$role" \
    > bamboo-specs/src/main/java/lab/shared/ansible/roles/$role/tasks/main.yml
  printf -- '---\n' > bamboo-specs/src/main/java/lab/shared/ansible/roles/$role/defaults/main.yml
done
```

- [ ] **Step 5: Prove the data and splunk VMs are outside the k8s play**

```bash
cd bamboo-specs/src/main/java/lab/shared/ansible
cat > inventory/_syntaxcheck.ini <<'EOF'
[mgmt]
m1 ansible_host=127.0.0.1

[compute]
c1 ansible_host=127.0.0.2

[data]
d1 ansible_host=127.0.0.3

[splunk]
s1 ansible_host=127.0.0.4

[k8s_nodes:children]
mgmt
compute
EOF
ANSIBLE_CONFIG=ansible.cfg ansible-playbook site.yml -i inventory/_syntaxcheck.ini \
  --syntax-check -e cluster_type=k8s -e addons=keycloak,hdfs,splunk
ANSIBLE_CONFIG=ansible.cfg ansible-playbook site.yml -i inventory/_syntaxcheck.ini \
  --list-hosts -e cluster_type=k8s -e addons=keycloak,hdfs,splunk
```
Expected: syntax check passes. In `--list-hosts`, the "Kubernetes install" play
lists `m1` and `c1` only; "HDFS on the data nodes" lists `d1`; "Splunk on the
splunk nodes" lists `s1`.

Then: `rm inventory/_syntaxcheck.ini` (the inventory directory is gitignored, but
leave nothing behind).

- [ ] **Step 6: Lint**

Run: `cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/site.yml \
        bamboo-specs/src/main/java/lab/shared/ansible/roles/common \
        bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak \
        bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs \
        bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk
git commit -m "fix: target cluster roles at k8s_nodes so data and splunk VMs stay clean"
```

---

### Task 7: k9s and a default StorageClass on every k8s cluster

Two gaps in the base cluster, neither of them an addon. There is no StorageClass at all, so no PVC can ever bind — Keycloak's Postgres needs one in Task 9. And k9s is what makes a cluster navigable.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/defaults/main.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/tasks/main.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/tasks/k9s.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s/tasks/storage.yml`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py`
- Test: `bamboo-specs/src/test/python/test_verify.py` (exists — **append**, never overwrite: it holds 5 passing `nodes_ready` tests, including a `Ready,SchedulingDisabled` case)

**Interfaces:**
- Produces: `verify.default_storage_class(text: str) -> str` — the name of the default StorageClass in `kubectl get sc --no-headers` output, or `""`.
- Components reported: `{'name': 'k9s', 'version': k9s_version}` and `{'name': 'local-path-provisioner', 'version': k8s_local_path_version}`.

- [ ] **Step 1: Write the failing test**

`bamboo-specs/src/test/python/test_verify.py` already exists and holds 5 passing
`nodes_ready` tests. **Append** these; do not overwrite. It already has
`import verify` at the top — do not add a second one, and do not add further
`nodes_ready` cases, which are covered.

```python
def test_default_storage_class_finds_the_annotated_one():
    text = (
        "local-path (default)   rancher.io/local-path   Delete   "
        "WaitForFirstConsumer   false   3m\n"
    )
    assert verify.default_storage_class(text) == "local-path"


def test_default_storage_class_is_empty_when_none_is_default():
    text = "local-path   rancher.io/local-path   Delete   Immediate   false   3m\n"
    assert verify.default_storage_class(text) == ""


def test_default_storage_class_is_empty_when_there_are_no_classes():
    assert verify.default_storage_class("") == ""


def test_default_storage_class_ignores_a_provisioner_named_default():
    text = "fast   example.io/default   Delete   Immediate   false   3m\n"
    assert verify.default_storage_class(text) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -v`
Expected: FAIL — `default_storage_class` does not exist. The 5 pre-existing
`nodes_ready` tests must still pass.

- [ ] **Step 3: Add the parser and extend the k8s check**

In `$LAB/provisioncluster/scripts/verify.py`:

```python
def default_storage_class(text: str) -> str:
    """The default StorageClass name in `kubectl get sc --no-headers` output.

    kubectl renders the default-class annotation as a `(default)` suffix on the
    name, which lands in the second whitespace-separated column.
    """
    for line in text.splitlines():
        cols = line.split()
        if len(cols) > 1 and cols[1] == "(default)":
            return cols[0]
    return ""
```

At the end of `_verify_k8s`, after the nodes are Ready:

```python
    result = _ssh(mgmt_ip, "kubectl get storageclass --no-headers")
    if not default_storage_class(result.stdout):
        proc.die("no default StorageClass — local-path-provisioner did not install")
    result = _ssh(mgmt_ip, "k9s version --short")
    if result.returncode != 0:
        proc.die("k9s is not installed on the control plane node")
    print("default StorageClass and k9s present")
```

Move the `return` in `_verify_k8s`'s loop to a `break`, so the checks above run
after the loop rather than being skipped. The `proc.die` on timeout goes in the
loop's `else:` clause.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -v`
Expected: PASS — 9 tests (5 pre-existing plus the 4 added).

- [ ] **Step 5: Add the role defaults**

Append to `$LAB/shared/ansible/roles/k8s/defaults/main.yml`:

```yaml
k9s_version: "0.32.7"
k8s_local_path_version: "v0.0.31"
k8s_local_path_url: >-
  https://raw.githubusercontent.com/rancher/local-path-provisioner/{{ k8s_local_path_version }}/deploy/local-path-storage.yaml
```

- [ ] **Step 6: Install k9s**

Create `$LAB/shared/ansible/roles/k8s/tasks/k9s.yml`:

```yaml
---
# Multipass VMs are arm64 on Apple Silicon and amd64 elsewhere; the release
# asset names differ, so pick from the host's own architecture.
- name: Resolve the k9s release asset for this architecture
  ansible.builtin.set_fact:
    k9s_asset: "k9s_Linux_{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}.tar.gz"

- name: Download k9s
  ansible.builtin.get_url:
    url: "https://github.com/derailed/k9s/releases/download/v{{ k9s_version }}/{{ k9s_asset }}"
    dest: "/tmp/{{ k9s_asset }}"
    mode: "0644"

- name: Install k9s
  ansible.builtin.unarchive:
    src: "/tmp/{{ k9s_asset }}"
    dest: /usr/local/bin
    remote_src: true
    include: [k9s]
    owner: root
    group: root
    mode: "0755"
    creates: /usr/local/bin/k9s
```

- [ ] **Step 7: Install the storage provisioner**

Create `$LAB/shared/ansible/roles/k8s/tasks/storage.yml`:

```yaml
---
# A kubeadm cluster ships no StorageClass, so every PVC stays Pending forever.
# local-path-provisioner is the smallest thing that fixes that on single-host
# lab nodes: it hands out hostPath directories under /opt/local-path-provisioner.
- name: Install local-path-provisioner
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f {{ k8s_local_path_url }}
  register: k8s_local_path
  changed_when: "'created' in k8s_local_path.stdout or 'configured' in k8s_local_path.stdout"

- name: Make local-path the default StorageClass
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf patch storageclass local-path
    -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
  register: k8s_sc_default
  changed_when: "'patched' in k8s_sc_default.stdout"
  failed_when:
    - k8s_sc_default.rc != 0
    - "'not patched' not in k8s_sc_default.stdout"

- name: Wait for the provisioner to be ready
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf -n local-path-storage
    rollout status deployment/local-path-provisioner --timeout=180s
  changed_when: false
```

- [ ] **Step 8: Wire both into the role and report them**

In `$LAB/shared/ansible/roles/k8s/tasks/main.yml`, between the join step and the
component report:

```yaml
- name: Install k9s on the control plane
  ansible.builtin.import_tasks: k9s.yml
  when: inventory_hostname in groups['mgmt']

- name: Install the default StorageClass
  ansible.builtin.import_tasks: storage.yml
  when: inventory_hostname == groups['mgmt'][0]
```

and extend the reported components:

```yaml
- name: Report installed components  # noqa: var-naming[no-role-prefix]
  ansible.builtin.set_fact:
    forgelab_components: >-
      {{ forgelab_components | default([]) + [{'name': 'kubernetes', 'version': k8s_version},
      {'name': 'containerd'}, {'name': 'flannel', 'version': 'latest'},
      {'name': 'k9s', 'version': k9s_version},
      {'name': 'local-path-provisioner', 'version': k8s_local_path_version}] }}
```

- [ ] **Step 9: Lint and provision for real**

```bash
cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint && cd -
make provision CLUSTER=lab1 TYPE=k8s ADDONS=
```
Expected: provision succeeds; verify prints `default StorageClass and k9s present`;
`cluster_registered/lab1_cluster_info.yml` lists `k9s` and `local-path-provisioner`.
Confirm by hand: `ssh lab1-mgmt-1 k9s version`.

- [ ] **Step 10: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/roles/k8s \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py \
        bamboo-specs/src/test/python/test_verify.py
git commit -m "feat: k9s and a default StorageClass on every k8s cluster"
```

---

### Task 8: Keycloak verification

Written before the role, so the role has something to satisfy. The proof is not "a pod is running" — it is that a client can obtain a token for the seeded user.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py`
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py`
- Test: `bamboo-specs/src/test/python/test_verify.py`

**Interfaces:**
- Consumes: `credentials.read(cluster)` (Task 4); `inventory.mgmt_ip` (Task 1).
- Produces:
  - `verify.KEYCLOAK_PORT = 30080`, `verify.KEYCLOAK_REALM = "forgelab"`, `verify.KEYCLOAK_CLIENT = "app"`, `verify.KEYCLOAK_USER = "labuser"` — Task 9's role must use these exact values.
  - `verify.field_from(payload: str, key: str) -> str`
  - `verify.main(argv)` now takes `<cluster> <cluster_type> [addons]`.

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_verify.py`:

```python
def test_field_from_reads_the_access_token():
    assert verify.field_from('{"access_token": "abc.def"}', "access_token") == "abc.def"


def test_field_from_reads_the_issuer():
    payload = '{"issuer": "http://1.2.3.4:30080/realms/forgelab"}'
    assert verify.field_from(payload, "issuer").endswith("/realms/forgelab")


def test_field_from_is_empty_when_the_key_is_missing():
    assert verify.field_from('{"error": "invalid_grant"}', "access_token") == ""


def test_field_from_is_empty_on_malformed_json():
    assert verify.field_from("<html>404</html>", "access_token") == ""


def test_field_from_is_empty_on_a_json_array():
    assert verify.field_from("[1, 2]", "access_token") == ""


def test_field_from_is_empty_on_a_non_string_value():
    assert verify.field_from('{"access_token": 5}', "access_token") == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -k field_from -v`
Expected: FAIL — `field_from` does not exist.

- [ ] **Step 3: Add the parser and the Keycloak check**

In `$LAB/provisioncluster/scripts/verify.py`, add `json`, `urllib.parse` and
`credentials` to the imports, then:

```python
# Task 9's role must seed exactly these — verify and the role are one contract.
KEYCLOAK_PORT = 30080
KEYCLOAK_REALM = "forgelab"
KEYCLOAK_CLIENT = "app"
KEYCLOAK_USER = "labuser"


def field_from(payload: str, key: str) -> str:
    """A string field out of a JSON object, or "" for anything else."""
    try:
        data = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def _http(url: str, form=None) -> str:
    """GET, or POST a form. Returns the body, or "" on any failure."""
    data = urllib.parse.urlencode(form).encode() if form else None
    try:
        with urllib.request.urlopen(url, data=data, timeout=INTERVAL_SECONDS) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, OSError):
        return ""


def _verify_keycloak(mgmt_ip: str, password: str):
    base = f"http://{mgmt_ip}:{KEYCLOAK_PORT}/realms/{KEYCLOAK_REALM}"
    print(f"==> verify: keycloak realm at {base}")
    for _ in range(ATTEMPTS):
        if field_from(_http(f"{base}/.well-known/openid-configuration"), "issuer"):
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"keycloak realm '{KEYCLOAK_REALM}' never published a discovery document")

    if not password:
        proc.die("no keycloak_app_user_password in the cluster's credentials file")
    body = _http(
        f"{base}/protocol/openid-connect/token",
        {
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT,
            "username": KEYCLOAK_USER,
            "password": password,
        },
    )
    if not field_from(body, "access_token"):
        proc.die(f"keycloak issued no access_token for '{KEYCLOAK_USER}'")
    print(f"keycloak issued a token for {KEYCLOAK_USER}@{KEYCLOAK_REALM}")
```

Rewrite `main` to read the addon list and dispatch:

```python
def main(argv):
    if len(argv) < 2:
        proc.die("usage: verify.py <cluster_name> <cluster_type> [addons]")
    cluster, cluster_type = argv[0], argv[1]
    addons = [a for a in (argv[2] if len(argv) > 2 else "").split(",") if a]

    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster}")
    text = inv.read_text()
    mgmt_ip = inventory.mgmt_ip(text)
    if not mgmt_ip:
        proc.die("no mgmt host in inventory")

    if cluster_type == "k8s":
        _verify_k8s(mgmt_ip)
    elif cluster_type == "dcos":
        _verify_dcos(mgmt_ip)
    else:
        proc.die(f"unknown cluster_type: {cluster_type}")

    secrets_values = credentials.read(cluster)
    if "keycloak" in addons:
        _verify_keycloak(mgmt_ip, secrets_values.get("keycloak_app_user_password", ""))
```

Tasks 10 and 12 add their branches here.

- [ ] **Step 4: Pass the addon list from provision.py**

In `$LAB/provisioncluster/scripts/provision.py`, Stage 4:

```python
    proc.run(
        sys.executable,
        Path(__file__).resolve().parent / "verify.py",
        cluster,
        cluster_type,
        ",".join(addons),
    )
```

- [ ] **Step 5: Run the suite**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py \
        bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py \
        bamboo-specs/src/test/python/test_verify.py
git commit -m "feat: verify keycloak by obtaining a token for the seeded user"
```

---

### Task 9: The Keycloak role

**Deviation from the spec, and why:** the spec says `start --optimized`. That requires a prior `kc.sh build` baked into a custom image, and `start` refuses to run over plain HTTP without `KC_HOSTNAME` plus hostname/proxy settings. The stock image's `start --http-enabled=true --hostname-strict=false` gives the same behaviour behind a NodePort with no image build, which is the right trade for a lab.

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak/defaults/main.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak/tasks/main.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak/templates/keycloak.yml.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak/tasks/seed.yml`

**Interfaces:**
- Consumes: `keycloak_admin_password` and `keycloak_app_user_password` from the extra-vars payload (Task 5); the default StorageClass from Task 7; the constants fixed by Task 8 (`forgelab`, `app`, `labuser`, NodePort 30080).
- Produces: the component `{'name': 'keycloak', 'version': ..., 'url': ..., 'realm': ..., 'client_id': ...}`.

- [ ] **Step 1: Write the defaults**

`$LAB/shared/ansible/roles/keycloak/defaults/main.yml`:

```yaml
---
keycloak_version: "26.0.7"
keycloak_namespace: keycloak
keycloak_node_port: 30080
keycloak_realm: forgelab
keycloak_client_id: app
keycloak_user: labuser
keycloak_db_storage: 2Gi
keycloak_manifest: /etc/forgelab/keycloak.yml
```

- [ ] **Step 2: Write the manifest template**

`$LAB/shared/ansible/roles/keycloak/templates/keycloak.yml.j2`:

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: {{ keycloak_namespace }}
---
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-secrets
  namespace: {{ keycloak_namespace }}
type: Opaque
stringData:
  admin-password: "{{ keycloak_admin_password }}"
  db-password: "{{ keycloak_admin_password }}"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: keycloak-db
  namespace: {{ keycloak_namespace }}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: {{ keycloak_db_storage }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak-db
  namespace: {{ keycloak_namespace }}
spec:
  replicas: 1
  selector:
    matchLabels: {app: keycloak-db}
  strategy: {type: Recreate}
  template:
    metadata:
      labels: {app: keycloak-db}
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB
              value: keycloak
            - name: POSTGRES_USER
              value: keycloak
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: {name: keycloak-secrets, key: db-password}
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: keycloak-db}
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak-db
  namespace: {{ keycloak_namespace }}
spec:
  selector: {app: keycloak-db}
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: {{ keycloak_namespace }}
spec:
  replicas: 1
  selector:
    matchLabels: {app: keycloak}
  template:
    metadata:
      labels: {app: keycloak}
    spec:
      containers:
        - name: keycloak
          image: quay.io/keycloak/keycloak:{{ keycloak_version }}
          # start-dev is avoided so the lab exercises the production entrypoint;
          # the two flags are what let it serve plain HTTP behind a NodePort.
          args: ["start", "--http-enabled=true", "--hostname-strict=false"]
          env:
            - name: KC_DB
              value: postgres
            - name: KC_DB_URL
              value: jdbc:postgresql://keycloak-db:5432/keycloak
            - name: KC_DB_USERNAME
              value: keycloak
            - name: KC_DB_PASSWORD
              valueFrom:
                secretKeyRef: {name: keycloak-secrets, key: db-password}
            - name: KC_BOOTSTRAP_ADMIN_USERNAME
              value: admin
            - name: KC_BOOTSTRAP_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef: {name: keycloak-secrets, key: admin-password}
            - name: KC_HEALTH_ENABLED
              value: "true"
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: {path: /realms/master, port: 8080}
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 30
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: {{ keycloak_namespace }}
spec:
  type: NodePort
  selector: {app: keycloak}
  ports:
    - port: 8080
      targetPort: 8080
      nodePort: {{ keycloak_node_port }}
```

- [ ] **Step 3: Write the seed tasks**

`$LAB/shared/ansible/roles/keycloak/tasks/seed.yml`:

```yaml
---
# kcadm.sh runs inside the pod so the admin password never reaches this VM's
# process table. `|| true`-style idempotency is avoided: creating an existing
# realm exits non-zero with a message we match on instead.
- name: Log kcadm in
  ansible.builtin.command:
    argv:
      - kubectl
      - --kubeconfig=/etc/kubernetes/admin.conf
      - -n
      - "{{ keycloak_namespace }}"
      - exec
      - deploy/keycloak
      - --
      - /opt/keycloak/bin/kcadm.sh
      - config
      - credentials
      - --server=http://localhost:8080
      - --realm=master
      - --user=admin
      - "--password={{ keycloak_admin_password }}"
  changed_when: false
  no_log: true

- name: Create the lab realm
  ansible.builtin.command:
    argv:
      - kubectl
      - --kubeconfig=/etc/kubernetes/admin.conf
      - -n
      - "{{ keycloak_namespace }}"
      - exec
      - deploy/keycloak
      - --
      - /opt/keycloak/bin/kcadm.sh
      - create
      - realms
      - -s
      - "realm={{ keycloak_realm }}"
      - -s
      - enabled=true
  register: keycloak_realm_create
  changed_when: keycloak_realm_create.rc == 0
  failed_when:
    - keycloak_realm_create.rc != 0
    - "'already exists' not in keycloak_realm_create.stderr"

- name: Create the public OIDC client
  ansible.builtin.command:
    argv:
      - kubectl
      - --kubeconfig=/etc/kubernetes/admin.conf
      - -n
      - "{{ keycloak_namespace }}"
      - exec
      - deploy/keycloak
      - --
      - /opt/keycloak/bin/kcadm.sh
      - create
      - clients
      - -r
      - "{{ keycloak_realm }}"
      - -s
      - "clientId={{ keycloak_client_id }}"
      - -s
      - publicClient=true
      - -s
      - directAccessGrantsEnabled=true
      - -s
      - 'redirectUris=["*"]'
  register: keycloak_client_create
  changed_when: keycloak_client_create.rc == 0
  failed_when:
    - keycloak_client_create.rc != 0
    - "'already exists' not in keycloak_client_create.stderr"

- name: Create the test user
  ansible.builtin.command:
    argv:
      - kubectl
      - --kubeconfig=/etc/kubernetes/admin.conf
      - -n
      - "{{ keycloak_namespace }}"
      - exec
      - deploy/keycloak
      - --
      - /opt/keycloak/bin/kcadm.sh
      - create
      - users
      - -r
      - "{{ keycloak_realm }}"
      - -s
      - "username={{ keycloak_user }}"
      - -s
      - enabled=true
  register: keycloak_user_create
  changed_when: keycloak_user_create.rc == 0
  failed_when:
    - keycloak_user_create.rc != 0
    - "'already exists' not in keycloak_user_create.stderr"

- name: Set the test user's password
  ansible.builtin.command:
    argv:
      - kubectl
      - --kubeconfig=/etc/kubernetes/admin.conf
      - -n
      - "{{ keycloak_namespace }}"
      - exec
      - deploy/keycloak
      - --
      - /opt/keycloak/bin/kcadm.sh
      - set-password
      - -r
      - "{{ keycloak_realm }}"
      - --username
      - "{{ keycloak_user }}"
      - --new-password
      - "{{ keycloak_app_user_password }}"
  changed_when: true
  no_log: true
```

`directAccessGrantsEnabled=true` is what makes Task 8's password grant work; without
it the verifier gets `unauthorized_client`.

- [ ] **Step 4: Write the role's main tasks**

`$LAB/shared/ansible/roles/keycloak/tasks/main.yml`:

```yaml
---
- name: Directory for the rendered manifest
  ansible.builtin.file:
    path: "{{ keycloak_manifest | dirname }}"
    state: directory
    mode: "0700"

- name: Render the Keycloak manifest
  ansible.builtin.template:
    src: keycloak.yml.j2
    dest: "{{ keycloak_manifest }}"
    mode: "0600"
  no_log: true

- name: Apply the Keycloak manifest
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf apply -f {{ keycloak_manifest }}
  register: keycloak_apply
  changed_when: "'created' in keycloak_apply.stdout or 'configured' in keycloak_apply.stdout"

- name: Wait for the database
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf -n {{ keycloak_namespace }}
    rollout status deployment/keycloak-db --timeout=300s
  changed_when: false

- name: Wait for Keycloak
  ansible.builtin.command: >-
    kubectl --kubeconfig /etc/kubernetes/admin.conf -n {{ keycloak_namespace }}
    rollout status deployment/keycloak --timeout=600s
  changed_when: false

- name: Seed the realm, client and user
  ansible.builtin.import_tasks: seed.yml

- name: Report installed components  # noqa: var-naming[no-role-prefix]
  ansible.builtin.set_fact:
    forgelab_components: >-
      {{ forgelab_components | default([]) + [{'name': 'keycloak',
      'version': keycloak_version,
      'url': 'http://' ~ ansible_host ~ ':' ~ keycloak_node_port,
      'realm': keycloak_realm,
      'client_id': keycloak_client_id}] }}
```

- [ ] **Step 5: Lint**

Run: `cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint`
Expected: no findings. `no_log: true` on every task handling a password is required —
if `ansible-lint` flags anything else, fix it rather than adding a skip.

- [ ] **Step 6: Install against a live cluster**

```bash
make provision CLUSTER=lab1 TYPE=k8s ADDONS=keycloak
```
Expected: verify prints `keycloak issued a token for labuser@forgelab`.
`cluster_registered/lab1_cluster_info.yml` lists the keycloak component with its
URL, realm and client id, plus a `credentials:` pointer. Confirm the admin console
by hand at the reported URL with `admin` and the `keycloak_admin_password` from
`~/.forgelab/lab1-credentials.yml`.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/roles/keycloak
git commit -m "feat: keycloak on the cluster with a seeded realm, client and user"
```

---

### Task 10: HDFS verification

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py`
- Test: `bamboo-specs/src/test/python/test_verify.py`

**Interfaces:**
- Consumes: `inventory.first_ip(text, "data")` and `inventory.group_ips(text, "data")` (Task 1).
- Produces:
  - `verify.live_datanodes(report: str) -> int`
  - `verify.HDFS_APP_DIR = "/user/app"` — Task 11's role must create exactly this.

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_verify.py`:

```python
DFSADMIN_REPORT = """Configured Capacity: 126421467136 (117.74 GB)
Present Capacity: 112233445566 (104.5 GB)
DFS Remaining: 112233445566 (104.5 GB)

-------------------------------------------------
Live datanodes (3):

Name: 192.168.252.21:9866 (lab1-data-1)
Hostname: lab1-data-1
Decommission Status : Normal
"""


def test_live_datanodes_counts_the_reported_nodes():
    assert verify.live_datanodes(DFSADMIN_REPORT) == 3


def test_live_datanodes_is_zero_when_the_section_is_absent():
    assert verify.live_datanodes("Configured Capacity: 0 (0 B)\n") == 0


def test_live_datanodes_reads_a_single_node_cluster():
    assert verify.live_datanodes("Live datanodes (1):\n") == 1


def test_live_datanodes_is_zero_on_an_empty_report():
    assert verify.live_datanodes("") == 0


def test_live_datanodes_ignores_the_dead_datanodes_section():
    text = "Live datanodes (2):\n\nDead datanodes (5):\n"
    assert verify.live_datanodes(text) == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -k live_datanodes -v`
Expected: FAIL — `live_datanodes` does not exist.

- [ ] **Step 3: Add the parser and the check**

In `$LAB/provisioncluster/scripts/verify.py` (add `import re`):

```python
HDFS_APP_DIR = "/user/app"
_LIVE_DATANODES_RE = re.compile(r"Live datanodes \((\d+)\)")


def live_datanodes(report: str) -> int:
    """The count from `Live datanodes (N):` in `hdfs dfsadmin -report` output."""
    match = _LIVE_DATANODES_RE.search(report)
    return int(match.group(1)) if match else 0


def _verify_hdfs(data_ip: str, expected: int):
    print(f"==> verify: {expected} live datanodes on {data_ip}")
    for _ in range(ATTEMPTS):
        result = _ssh(data_ip, "hdfs dfsadmin -report")
        if result.returncode == 0 and live_datanodes(result.stdout) >= expected:
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"fewer than {expected} live datanodes within timeout")

    # A report can be healthy while writes fail — prove a roundtrip too.
    token = "forgelab-verify"
    result = _ssh(
        data_ip,
        f"printf '{token}' | hdfs dfs -put -f - {HDFS_APP_DIR}/verify.txt "
        f"&& hdfs dfs -cat {HDFS_APP_DIR}/verify.txt",
    )
    if result.returncode != 0 or token not in result.stdout:
        proc.die(f"could not write and read back {HDFS_APP_DIR}/verify.txt")
    print(f"hdfs roundtripped a file through {HDFS_APP_DIR}")
```

In `main`, after the Keycloak branch:

```python
    if "hdfs" in addons:
        data_ip = inventory.first_ip(text, "data")
        if not data_ip:
            proc.die("hdfs is enabled but the inventory has no data hosts")
        _verify_hdfs(data_ip, len(inventory.group_ips(text, "data")))
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py \
        bamboo-specs/src/test/python/test_verify.py
git commit -m "feat: verify hdfs by live datanode count and a file roundtrip"
```

---

### Task 11: The HDFS role

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/defaults/main.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/tasks/main.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/tasks/install.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/tasks/namenode.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/core-site.xml.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/hdfs-site.xml.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/hadoop-env.sh.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/hdfs-namenode.service.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs/templates/hdfs-datanode.service.j2`

**Interfaces:**
- Consumes: the `data` inventory group (Task 1); `HDFS_APP_DIR = /user/app` (Task 10).
- Produces: the component `{'name': 'hdfs', 'version': ..., 'namenode': 'hdfs://<data-1>:8020', 'ui': 'http://<data-1>:9870'}`. A profile script at `/etc/profile.d/hadoop.sh` so `hdfs` is on the PATH for the `ubuntu` user Task 10's verifier logs in as.

- [ ] **Step 1: Confirm the download URL resolves**

```bash
curl -sIL https://dlcdn.apache.org/hadoop/common/hadoop-3.4.1/hadoop-3.4.1.tar.gz | head -1
```
Expected: `HTTP/2 200`. If Apache has rotated 3.4.1 out of `dlcdn`, take the current
3.4.x from https://dlcdn.apache.org/hadoop/common/ and use that version throughout.
Hadoop ships a single architecture-independent tarball, so arm64 needs nothing special.

- [ ] **Step 2: Write the defaults**

`$LAB/shared/ansible/roles/hdfs/defaults/main.yml`:

```yaml
---
hdfs_version: "3.4.1"
hdfs_mirror: "https://dlcdn.apache.org/hadoop/common"
hdfs_home: /opt/hadoop
hdfs_data_dir: /var/lib/hadoop
hdfs_user: hdfs
hdfs_namenode_port: 8020
hdfs_ui_port: 9870
hdfs_replication: 2
hdfs_app_dir: /user/app
hdfs_app_owner: ubuntu
```

- [ ] **Step 3: Write the templates**

`templates/core-site.xml.j2`:

```xml
<?xml version="1.0"?>
<configuration>
  <property>
    <name>fs.defaultFS</name>
    <value>hdfs://{{ hostvars[groups['data'][0]].ansible_host }}:{{ hdfs_namenode_port }}</value>
  </property>
  <property>
    <name>hadoop.tmp.dir</name>
    <value>{{ hdfs_data_dir }}/tmp</value>
  </property>
</configuration>
```

`templates/hdfs-site.xml.j2`:

```xml
<?xml version="1.0"?>
<configuration>
  <property>
    <name>dfs.replication</name>
    <value>{{ hdfs_replication }}</value>
  </property>
  <property>
    <name>dfs.namenode.name.dir</name>
    <value>file://{{ hdfs_data_dir }}/name</value>
  </property>
  <property>
    <name>dfs.datanode.data.dir</name>
    <value>file://{{ hdfs_data_dir }}/data</value>
  </property>
  <property>
    <name>dfs.namenode.rpc-bind-host</name>
    <value>0.0.0.0</value>
  </property>
  <property>
    <name>dfs.namenode.http-address</name>
    <value>0.0.0.0:{{ hdfs_ui_port }}</value>
  </property>
  <!-- Lab clusters have no DNS; datanodes register by address. -->
  <property>
    <name>dfs.namenode.datanode.registration.ip-hostname-check</name>
    <value>false</value>
  </property>
</configuration>
```

`templates/hadoop-env.sh.j2`:

```bash
export JAVA_HOME={{ hdfs_java_home }}
export HADOOP_HOME={{ hdfs_home }}
export HADOOP_CONF_DIR={{ hdfs_home }}/etc/hadoop
export HDFS_NAMENODE_USER={{ hdfs_user }}
export HDFS_DATANODE_USER={{ hdfs_user }}
export PATH=$PATH:{{ hdfs_home }}/bin
```

`templates/hdfs-namenode.service.j2`:

```ini
[Unit]
Description=HDFS NameNode
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User={{ hdfs_user }}
Environment=JAVA_HOME={{ hdfs_java_home }}
Environment=HADOOP_HOME={{ hdfs_home }}
Environment=HADOOP_CONF_DIR={{ hdfs_home }}/etc/hadoop
Environment=HADOOP_PID_DIR={{ hdfs_data_dir }}/pid
ExecStart={{ hdfs_home }}/bin/hdfs --daemon start namenode
ExecStop={{ hdfs_home }}/bin/hdfs --daemon stop namenode
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`templates/hdfs-datanode.service.j2` — identical but with `Description=HDFS DataNode`
and `namenode` replaced by `datanode` in both `ExecStart` and `ExecStop`.

- [ ] **Step 4: Write the install tasks**

`$LAB/shared/ansible/roles/hdfs/tasks/install.yml`:

```yaml
---
- name: Install a JRE
  ansible.builtin.apt:
    name: openjdk-17-jre-headless
    state: present
    update_cache: true

- name: Locate JAVA_HOME
  ansible.builtin.shell: |
    set -eo pipefail
    dirname "$(dirname "$(readlink -f "$(command -v java)")")"
  args: {executable: /bin/bash}
  register: hdfs_java
  changed_when: false

- name: Remember JAVA_HOME
  ansible.builtin.set_fact:
    hdfs_java_home: "{{ hdfs_java.stdout }}"

- name: Service account
  ansible.builtin.user:
    name: "{{ hdfs_user }}"
    system: true
    shell: /usr/sbin/nologin
    home: "{{ hdfs_data_dir }}"
    create_home: false

- name: Data directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ hdfs_user }}"
    group: "{{ hdfs_user }}"
    mode: "0755"
  loop:
    - "{{ hdfs_data_dir }}"
    - "{{ hdfs_data_dir }}/name"
    - "{{ hdfs_data_dir }}/data"
    - "{{ hdfs_data_dir }}/tmp"
    - "{{ hdfs_data_dir }}/pid"
    - "{{ hdfs_home }}"

- name: Download and unpack Hadoop
  ansible.builtin.unarchive:
    src: "{{ hdfs_mirror }}/hadoop-{{ hdfs_version }}/hadoop-{{ hdfs_version }}.tar.gz"
    dest: "{{ hdfs_home }}"
    remote_src: true
    extra_opts: [--strip-components=1]
    owner: "{{ hdfs_user }}"
    group: "{{ hdfs_user }}"
    creates: "{{ hdfs_home }}/bin/hdfs"

- name: Configuration
  ansible.builtin.template:
    src: "{{ item }}.j2"
    dest: "{{ hdfs_home }}/etc/hadoop/{{ item }}"
    owner: "{{ hdfs_user }}"
    group: "{{ hdfs_user }}"
    mode: "0644"
  loop: [core-site.xml, hdfs-site.xml, hadoop-env.sh]
  notify: [Restart hdfs namenode, Restart hdfs datanode]

# verify.py logs in as ubuntu and runs `hdfs dfs`; without this it is not on PATH.
- name: Hadoop on every user's PATH
  ansible.builtin.template:
    src: hadoop-env.sh.j2
    dest: /etc/profile.d/hadoop.sh
    mode: "0644"

- name: Systemd units
  ansible.builtin.template:
    src: "{{ item }}.j2"
    dest: "/etc/systemd/system/{{ item }}"
    mode: "0644"
  loop: [hdfs-namenode.service, hdfs-datanode.service]
```

- [ ] **Step 5: Write the namenode tasks**

`$LAB/shared/ansible/roles/hdfs/tasks/namenode.yml`:

```yaml
---
# `creates:` is load-bearing — a second format wipes the filesystem.
- name: Format the NameNode
  ansible.builtin.command: "{{ hdfs_home }}/bin/hdfs namenode -format -nonInteractive"
  args:
    creates: "{{ hdfs_data_dir }}/name/current/VERSION"
  become_user: "{{ hdfs_user }}"
  environment:
    JAVA_HOME: "{{ hdfs_java_home }}"
    HADOOP_CONF_DIR: "{{ hdfs_home }}/etc/hadoop"

- name: Start the NameNode
  ansible.builtin.systemd:
    name: hdfs-namenode
    state: started
    enabled: true
    daemon_reload: true

- name: Wait for the NameNode RPC port
  ansible.builtin.wait_for:
    port: "{{ hdfs_namenode_port }}"
    timeout: 120

- name: Create the application directory
  ansible.builtin.command: >-
    {{ hdfs_home }}/bin/hdfs dfs -mkdir -p {{ hdfs_app_dir }}
  become_user: "{{ hdfs_user }}"
  environment:
    JAVA_HOME: "{{ hdfs_java_home }}"
    HADOOP_CONF_DIR: "{{ hdfs_home }}/etc/hadoop"
  register: hdfs_mkdir
  changed_when: hdfs_mkdir.rc == 0

- name: Hand the application directory to the login user
  ansible.builtin.command: >-
    {{ hdfs_home }}/bin/hdfs dfs -chown -R {{ hdfs_app_owner }} {{ hdfs_app_dir }}
  become_user: "{{ hdfs_user }}"
  environment:
    JAVA_HOME: "{{ hdfs_java_home }}"
    HADOOP_CONF_DIR: "{{ hdfs_home }}/etc/hadoop"
  changed_when: true
```

- [ ] **Step 6: Write the role's main tasks and handlers**

`$LAB/shared/ansible/roles/hdfs/tasks/main.yml`:

```yaml
---
- name: Install Hadoop on every data node
  ansible.builtin.import_tasks: install.yml

- name: Bring up the NameNode
  ansible.builtin.import_tasks: namenode.yml
  when: inventory_hostname == groups['data'][0]

- name: Start the DataNode
  ansible.builtin.systemd:
    name: hdfs-datanode
    state: started
    enabled: true
    daemon_reload: true

- name: Report installed components  # noqa: var-naming[no-role-prefix]
  ansible.builtin.set_fact:
    forgelab_components: >-
      {{ forgelab_components | default([]) + [{'name': 'hdfs',
      'version': hdfs_version,
      'namenode': 'hdfs://' ~ hostvars[groups['data'][0]].ansible_host ~ ':' ~ hdfs_namenode_port,
      'ui': 'http://' ~ hostvars[groups['data'][0]].ansible_host ~ ':' ~ hdfs_ui_port}] }}
```

Create `$LAB/shared/ansible/roles/hdfs/handlers/main.yml`:

```yaml
---
- name: Restart hdfs namenode
  ansible.builtin.systemd:
    name: hdfs-namenode
    state: restarted
    daemon_reload: true
  when: inventory_hostname == groups['data'][0]

- name: Restart hdfs datanode
  ansible.builtin.systemd:
    name: hdfs-datanode
    state: restarted
    daemon_reload: true
```

- [ ] **Step 7: Lint**

Run: `cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint`
Expected: no findings.

- [ ] **Step 8: Install against a live cluster**

```bash
make provision CLUSTER=lab1 TYPE=k8s ADDONS=hdfs
```
Expected: verify prints `hdfs roundtripped a file through /user/app`. The info file
lists the hdfs component with its NameNode URI and UI address. Confirm the UI by
hand at `http://<lab1-data-1>:9870` — it should show 3 live nodes.

- [ ] **Step 9: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/roles/hdfs
git commit -m "feat: hdfs on the data nodes, with /user/app ready for an application"
```

---

### Task 12: Splunk verification

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py`
- Test: `bamboo-specs/src/test/python/test_verify.py`

**Interfaces:**
- Consumes: `inventory.first_ip(text, "splunk")`, `inventory.group_ips(text, "splunk")`; `credentials.read(cluster)["splunk_admin_password"]`.
- Produces:
  - `verify.search_peers_up(text: str) -> int`
  - `verify.stats_count(csv_text: str) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_verify.py`:

```python
SEARCH_SERVERS = """Server:192.168.252.32:8089
	Status:Up
	Cluster Label:
Server:192.168.252.33:8089
	Status:Up
	Cluster Label:
"""


def test_search_peers_up_counts_the_reachable_peers():
    assert verify.search_peers_up(SEARCH_SERVERS) == 2


def test_search_peers_up_ignores_a_down_peer():
    text = "Server:a:8089\n\tStatus:Up\nServer:b:8089\n\tStatus:Down\n"
    assert verify.search_peers_up(text) == 1


def test_search_peers_up_is_zero_with_no_peers():
    assert verify.search_peers_up("") == 0


def test_stats_count_reads_the_single_value():
    assert verify.stats_count('count\n1421\n') == 1421


def test_stats_count_tolerates_quoted_csv():
    assert verify.stats_count('"count"\n"7"\n') == 7


def test_stats_count_is_zero_when_the_search_returned_nothing():
    assert verify.stats_count("count\n") == 0


def test_stats_count_is_zero_on_unparseable_output():
    assert verify.stats_count("Login failed\n") == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest bamboo-specs/src/test/python/test_verify.py -k "search_peers or stats_count" -v`
Expected: FAIL — neither function exists.

- [ ] **Step 3: Add the parsers and the check**

In `$LAB/provisioncluster/scripts/verify.py`:

```python
SPLUNK_HOME = "/opt/splunk"
_PEER_UP_RE = re.compile(r"Status:\s*Up\b")


def search_peers_up(text: str) -> int:
    """Peers reporting Up in `splunk list search-server` output."""
    return len(_PEER_UP_RE.findall(text))


def stats_count(csv_text: str) -> int:
    """The value under the `count` header of `splunk search ... -output csv`."""
    rows = [row.strip() for row in csv_text.splitlines() if row.strip()]
    if len(rows) < 2:
        return 0
    try:
        return int(rows[1].strip('"'))
    except ValueError:
        return 0


def _verify_splunk(head_ip: str, expected_peers: int, password: str):
    if not password:
        proc.die("no splunk_admin_password in the cluster's credentials file")
    auth = f"-auth admin:{password}"
    print(f"==> verify: {expected_peers} search peers on {head_ip}")
    for _ in range(ATTEMPTS):
        result = _ssh(head_ip, f"{SPLUNK_HOME}/bin/splunk list search-server {auth}")
        if result.returncode == 0 and search_peers_up(result.stdout) >= expected_peers:
            break
        time.sleep(INTERVAL_SECONDS)
    else:
        proc.die(f"fewer than {expected_peers} search peers Up within timeout")

    # Forwarder data is the slow signal: the peers can be Up long before any
    # host has shipped an event.
    query = "search index=_internal earliest=-1h | stats count"
    for _ in range(ATTEMPTS):
        result = _ssh(
            head_ip, f"{SPLUNK_HOME}/bin/splunk search '{query}' -output csv {auth}"
        )
        if result.returncode == 0 and stats_count(result.stdout) > 0:
            print(f"splunk searched {stats_count(result.stdout)} events across its peers")
            return
        time.sleep(INTERVAL_SECONDS)
    proc.die("splunk returned no events from a distributed search within timeout")
```

The password reaches the remote command line here, on a disposable lab VM, because
`splunk` offers no file-based auth for one-shot CLI calls. It is never written to
this repository and never appears in the cluster info file.

In `main`, after the HDFS branch:

```python
    if "splunk" in addons:
        head_ip = inventory.first_ip(text, "splunk")
        if not head_ip:
            proc.die("splunk is enabled but the inventory has no splunk hosts")
        peers = max(len(inventory.group_ips(text, "splunk")) - 1, 0)
        _verify_splunk(head_ip, peers, secrets_values.get("splunk_admin_password", ""))
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest bamboo-specs/src/test/python -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py \
        bamboo-specs/src/test/python/test_verify.py
git commit -m "feat: verify splunk by peer count and a distributed search"
```

---

### Task 13: The Splunk role and universal forwarders

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk/defaults/main.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk/tasks/main.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk/tasks/indexer.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk/tasks/searchhead.yml`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk/templates/user-seed.conf.j2`
- Create: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/tasks/forwarder.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/tasks/main.yml`
- Modify: `bamboo-specs/src/main/java/lab/shared/ansible/roles/common/defaults/main.yml` (create)

**Interfaces:**
- Consumes: `splunk_admin_password` from extra-vars (Task 5); the `splunk` inventory group (Task 1); `SPLUNK_HOME = /opt/splunk` (Task 12).
- Produces: the component `{'name': 'splunk', 'version': ..., 'ui': 'http://<splunk-1>:8000', 'license': 'trial-60d'}`.

- [ ] **Step 1: Confirm the arm64 artifacts exist before writing anything**

Multipass VMs are arm64 on Apple Silicon. Splunk publishes arm64 Linux builds, but
the exact filenames change per release. Resolve both before continuing:

```bash
curl -sIL -o /dev/null -w '%{http_code} %{url_effective}\n' \
  'https://download.splunk.com/products/splunk/releases/9.3.2/linux/splunk-9.3.2-d8bb32809498-linux-2.6-arm64.deb'
curl -sIL -o /dev/null -w '%{http_code} %{url_effective}\n' \
  'https://download.splunk.com/products/universalforwarder/releases/9.3.2/linux/splunkforwarder-9.3.2-d8bb32809498-Linux-arm64.tgz'
```
Expected: `200` for both. If either 404s, browse https://www.splunk.com/en_us/download/splunk-enterprise.html
and https://www.splunk.com/en_us/download/universal-forwarder.html, take the current
arm64 `.deb` / `.tgz` URLs, and set `splunk_version`, `splunk_build` and the two URL
defaults below to match. **Do not proceed with a URL you have not resolved** — the
role will otherwise fail 20 minutes into a provision.

- [ ] **Step 2: Write the defaults**

`$LAB/shared/ansible/roles/splunk/defaults/main.yml`:

```yaml
---
splunk_version: "9.3.2"
splunk_build: "d8bb32809498"
splunk_home: /opt/splunk
splunk_user: splunk
splunk_web_port: 8000
splunk_mgmt_port: 8089
splunk_receive_port: 9997
splunk_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
splunk_deb_url: >-
  https://download.splunk.com/products/splunk/releases/{{ splunk_version }}/linux/splunk-{{ splunk_version }}-{{ splunk_build }}-linux-2.6-{{ splunk_arch }}.deb
```

`$LAB/shared/ansible/roles/common/defaults/main.yml`:

```yaml
---
splunk_version: "9.3.2"
splunk_build: "d8bb32809498"
splunk_forwarder_home: /opt/splunkforwarder
splunk_receive_port: 9997
splunk_forwarder_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
splunk_forwarder_url: >-
  https://download.splunk.com/products/universalforwarder/releases/{{ splunk_version }}/linux/splunkforwarder-{{ splunk_version }}-{{ splunk_build }}-Linux-{{ splunk_forwarder_arch }}.tgz
```

- [ ] **Step 3: Write the admin seed template**

`$LAB/shared/ansible/roles/splunk/templates/user-seed.conf.j2`:

```ini
[user_info]
USERNAME = admin
PASSWORD = {{ splunk_admin_password }}
```

Splunk consumes and deletes this file on first start. It is the only way to set the
admin password without an interactive prompt.

- [ ] **Step 4: Write the role's main tasks**

`$LAB/shared/ansible/roles/splunk/tasks/main.yml`:

```yaml
---
- name: Download Splunk Enterprise
  ansible.builtin.get_url:
    url: "{{ splunk_deb_url }}"
    dest: "/tmp/splunk-{{ splunk_version }}.deb"
    mode: "0644"

- name: Install Splunk Enterprise
  ansible.builtin.apt:
    deb: "/tmp/splunk-{{ splunk_version }}.deb"
    state: present

# Splunk consumes user-seed.conf on first start and writes splunk.secret. On a
# re-run the instance is already initialised, and re-seeding it would reset the
# admin password out from under the credentials file.
- name: Check whether Splunk has already been initialised
  ansible.builtin.stat:
    path: "{{ splunk_home }}/etc/auth/splunk.secret"
  register: splunk_initialised

- name: Seed the admin credentials
  ansible.builtin.template:
    src: user-seed.conf.j2
    dest: "{{ splunk_home }}/etc/system/local/user-seed.conf"
    owner: "{{ splunk_user }}"
    group: "{{ splunk_user }}"
    mode: "0600"
  no_log: true
  when: not splunk_initialised.stat.exists

- name: Accept the licence and enable boot-start
  ansible.builtin.command: >-
    {{ splunk_home }}/bin/splunk enable boot-start -user {{ splunk_user }}
    -systemd-managed 1 --accept-license --answer-yes --no-prompt
  args:
    creates: /etc/systemd/system/Splunkd.service

- name: Start Splunk
  ansible.builtin.systemd:
    name: Splunkd
    state: started
    enabled: true
    daemon_reload: true

- name: Wait for the management port
  ansible.builtin.wait_for:
    port: "{{ splunk_mgmt_port }}"
    timeout: 300

- name: Configure the indexers
  ansible.builtin.import_tasks: indexer.yml
  when: inventory_hostname != groups['splunk'][0]

- name: Configure the search head
  ansible.builtin.import_tasks: searchhead.yml
  when: inventory_hostname == groups['splunk'][0]

- name: Report installed components  # noqa: var-naming[no-role-prefix]
  ansible.builtin.set_fact:
    forgelab_components: >-
      {{ forgelab_components | default([]) + [{'name': 'splunk',
      'version': splunk_version,
      'ui': 'http://' ~ hostvars[groups['splunk'][0]].ansible_host ~ ':' ~ splunk_web_port,
      'license': 'trial-60d'}] }}
```

- [ ] **Step 5: Write the indexer tasks**

`$LAB/shared/ansible/roles/splunk/tasks/indexer.yml`:

```yaml
---
- name: Enable receiving from forwarders
  ansible.builtin.command: >-
    {{ splunk_home }}/bin/splunk enable listen {{ splunk_receive_port }}
    -auth admin:{{ splunk_admin_password }}
  register: splunk_listen
  changed_when: "'Listening for' in splunk_listen.stdout"
  failed_when:
    - splunk_listen.rc != 0
    - "'already exists' not in splunk_listen.stderr"
  no_log: true
```

- [ ] **Step 6: Write the search head tasks**

`$LAB/shared/ansible/roles/splunk/tasks/searchhead.yml`:

```yaml
---
# Distributed search, not index clustering: the head queries the two indexers
# directly. That is enough to exercise fan-out at lab scale.
- name: Add each indexer as a search peer
  ansible.builtin.command: >-
    {{ splunk_home }}/bin/splunk add search-server
    https://{{ hostvars[item].ansible_host }}:{{ splunk_mgmt_port }}
    -auth admin:{{ splunk_admin_password }}
    -remoteUsername admin -remotePassword {{ splunk_admin_password }}
  loop: "{{ groups['splunk'][1:] }}"
  register: splunk_peer
  changed_when: "'added' in splunk_peer.stdout | default('')"
  failed_when:
    - splunk_peer.rc != 0
    - "'already exists' not in splunk_peer.stderr | default('')"
  no_log: true
```

- [ ] **Step 7: Write the forwarder tasks**

`$LAB/shared/ansible/roles/common/tasks/forwarder.yml`:

```yaml
---
- name: Forwarder service account
  ansible.builtin.user:
    name: splunkfwd
    system: true
    shell: /usr/sbin/nologin
    home: "{{ splunk_forwarder_home }}"
    create_home: false

- name: Unpack the universal forwarder
  ansible.builtin.unarchive:
    src: "{{ splunk_forwarder_url }}"
    dest: /opt
    remote_src: true
    owner: splunkfwd
    group: splunkfwd
    creates: "{{ splunk_forwarder_home }}/bin/splunk"

- name: Check whether the forwarder has already been initialised
  ansible.builtin.stat:
    path: "{{ splunk_forwarder_home }}/etc/auth/splunk.secret"
  register: splunk_fwd_initialised

- name: Seed the forwarder admin credentials
  ansible.builtin.template:
    src: user-seed.conf.j2
    dest: "{{ splunk_forwarder_home }}/etc/system/local/user-seed.conf"
    owner: splunkfwd
    group: splunkfwd
    mode: "0600"
  no_log: true
  when: not splunk_fwd_initialised.stat.exists

- name: Accept the licence and enable boot-start
  ansible.builtin.command: >-
    {{ splunk_forwarder_home }}/bin/splunk enable boot-start -user splunkfwd
    -systemd-managed 1 --accept-license --answer-yes --no-prompt
  args:
    creates: /etc/systemd/system/SplunkForwarder.service

- name: Start the forwarder
  ansible.builtin.systemd:
    name: SplunkForwarder
    state: started
    enabled: true
    daemon_reload: true

- name: Forward to every indexer
  ansible.builtin.command: >-
    {{ splunk_forwarder_home }}/bin/splunk add forward-server
    {{ hostvars[item].ansible_host }}:{{ splunk_receive_port }}
    -auth admin:{{ splunk_admin_password }}
  loop: "{{ groups['splunk'][1:] }}"
  register: splunk_fwd
  changed_when: "'Added forwarding' in splunk_fwd.stdout | default('')"
  failed_when:
    - splunk_fwd.rc != 0
    - "'already present' not in splunk_fwd.stderr | default('')"
  no_log: true

- name: Monitor the system logs
  ansible.builtin.command: >-
    {{ splunk_forwarder_home }}/bin/splunk add monitor /var/log
    -auth admin:{{ splunk_admin_password }}
  register: splunk_monitor
  changed_when: "'Added monitor' in splunk_monitor.stdout | default('')"
  failed_when:
    - splunk_monitor.rc != 0
    - "'already' not in splunk_monitor.stderr | default('')"
  no_log: true
```

Copy `user-seed.conf.j2` into
`$LAB/shared/ansible/roles/common/templates/user-seed.conf.j2` — a role never reads
another role's templates.

- [ ] **Step 8: Gate the forwarder in `common`**

Append to `$LAB/shared/ansible/roles/common/tasks/main.yml`:

```yaml
# The forwarders are what give Splunk data with no application deployed. The
# splunk VMs are excluded: they are the destination, not a source.
- name: Universal forwarder
  ansible.builtin.include_tasks: forwarder.yml
  when:
    - "'splunk' in addons.split(',')"
    - inventory_hostname not in groups['splunk'] | default([])
```

- [ ] **Step 9: Lint**

Run: `cd bamboo-specs/src/main/java/lab/shared/ansible && ansible-lint`
Expected: no findings.

- [ ] **Step 10: Install against a live cluster**

```bash
make provision CLUSTER=lab1 TYPE=k8s ADDONS=splunk
```
Expected: verify prints `splunk searched N events across its peers` with N > 0.
The info file lists the splunk component with its UI address and `license: trial-60d`.
Confirm by hand: open the reported UI, log in as `admin` with `splunk_admin_password`
from `~/.forgelab/lab1-credentials.yml`, and run `index=_internal | stats count by host` —
mgmt, compute and data hosts should all appear.

- [ ] **Step 11: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/ansible/roles/splunk \
        bamboo-specs/src/main/java/lab/shared/ansible/roles/common
git commit -m "feat: splunk search head, indexers and universal forwarders"
```

---

### Task 14: The Bamboo plan variable, a full run, and the docs

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/provisioncluster/ProvisionClusterSpec.java`
- Modify: `bamboo-specs/src/test/java/lab/provisioncluster/ProvisionClusterSpecTest.java`
- Modify: `CLAUDE.md`
- Modify: `bamboo-specs/src/main/java/lab/README.md`

**Interfaces:**
- Consumes: `provision.py <cluster> <type> <addons>` from Task 2.
- Produces: plan variable `addons`, default `""`.

- [ ] **Step 1: Add the plan variable**

In `ProvisionClusterSpec.java`, extend `.variables(...)`:

```java
                .variables(
                        new Variable("cluster_name", "lab1"),
                        new Variable("cluster_type", ""),
                        new Variable("addons", ""))
```

and the script task body:

```java
                                new ScriptTask().description("provision cluster")
                                        .inlineBody("bamboo-specs/src/main/java/lab/provisioncluster/scripts/provision.py "
                                                + "\"${bamboo.cluster_name}\" \"${bamboo.cluster_type}\" "
                                                + "\"${bamboo.addons}\""))));
```

- [ ] **Step 2: Assert the variable exists**

Replace `ProvisionClusterSpecTest.java` with:

```java
package lab.provisioncluster;

import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class ProvisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
    }

    @Test
    public void planExposesTheAddonsVariable() {
        PlanProperties plan = EntityPropertiesBuilders.build(new ProvisionClusterSpec().plan());
        assertTrue(
                "the addons plan variable is how a build overrides the cluster's tfvars",
                plan.getVariables().stream().anyMatch(v -> "addons".equals(v.getName())));
    }
}
```

- [ ] **Step 3: Run the Java tests**

Run: `mvn -f bamboo-specs/pom.xml -q test`
Expected: PASS.

- [ ] **Step 4: Run the full lint**

Run: `make lint`
Expected: PASS — pytest, terraform fmt/validate, ansible-lint, mvn test.

- [ ] **Step 5: Provision everything, end to end**

```bash
make deprovision CLUSTER=lab1
make provision CLUSTER=lab1
```
`lab1.tfvars` has all three addons, so this builds 9 VMs and installs everything.
Expected: verify prints the k8s, Keycloak, HDFS and Splunk lines in that order, and
`cluster_registered/lab1_cluster_info.yml` lists 9 nodes and the components
`kubernetes`, `containerd`, `flannel`, `k9s`, `local-path-provisioner`, `keycloak`,
`hdfs`, `splunk`, plus a `credentials:` pointer and no password anywhere.

Then confirm the toggle actually gates:

```bash
make deprovision CLUSTER=lab1
make provision CLUSTER=lab1 ADDONS=
multipass list | grep -c lab1
```
Expected: 3 VMs, and an info file with no keycloak, hdfs or splunk component.

- [ ] **Step 6: Publish the plan**

Run: `make specs-publish`
Expected: the Provision plan updates; the `addons` variable is visible under
Plan Configuration → Variables in Bamboo.

- [ ] **Step 7: Update CLAUDE.md**

Under Commands, after the provision/deprovision entry:

```markdown
- `make addons CLUSTER=lab1 [TYPE=k8s] [ADDONS=hdfs]` — re-run the install stage
  only, against an existing cluster's inventory. Use it to iterate on an ansible
  role without a full rebuild
```

and extend the provision entry to mention `ADDONS=`.

In the Layout map, add to the `lab/shared/` bullet:

```markdown
  `python/forgelab/` (the lab's one library, stdlib only; `credentials.py` writes
  `~/.forgelab/<cluster>-credentials.yml`, referenced by the registry, never inlined),
```

Add a Conventions bullet:

```markdown
- Cluster addons are opt-in per cluster: `addons = "keycloak,hdfs,splunk"` in the
  cluster's tfvars, overridable by the `addons` Bamboo plan variable. The list
  gates the ansible roles AND zeroes the VM roles of disabled addons, so sizing
  and enablement cannot disagree. k9s is not an addon — it ships with the k8s
  role. Addon secrets live in `~/.forgelab/<cluster>-credentials.yml` (0600) and
  never in `cluster_registered/`
```

- [ ] **Step 8: Update lab/README.md**

Add `credentials.py` to the forgelab module table:

```markdown
| `credentials.py` | `~/.forgelab/<cluster>-credentials.yml`, per-cluster secrets |
```

Update the tree to show `scripts/{provision.py,install.py,verify.py}` under
`provisioncluster/`, and add the new roles under `shared/ansible/`.

- [ ] **Step 9: Commit**

```bash
git add bamboo-specs/src/main/java/lab/provisioncluster/ProvisionClusterSpec.java \
        bamboo-specs/src/test/java/lab/provisioncluster/ProvisionClusterSpecTest.java \
        CLAUDE.md bamboo-specs/src/main/java/lab/README.md
git commit -m "feat: expose the addons list as a Bamboo plan variable"
```

---

## Deviations from the spec

Both were found while planning and are called out in place; neither changes the design's intent.

1. **Kernel preparation stays in `roles/common`, gated on `k8s_nodes` membership**, rather than moving into the `k8s` role (Task 6). DC/OS needs the same preparation, and moving it would break `cluster_type=dcos`. The outcome is identical: data and splunk VMs are untouched.
2. **Keycloak runs `start --http-enabled=true --hostname-strict=false`**, not `start --optimized` (Task 9). `--optimized` needs a prior `kc.sh build` in a custom image, and `start` refuses plain HTTP without hostname configuration. The stock image with those two flags serves the same thing behind a NodePort with no image build.

---

## Revision, 2026-08-04: the splunk addon becomes opensearch

**Why.** Task 13's Step 1 hard gate resolved the Splunk download URLs and found that
Splunk Enterprise has **no Linux arm64 build at any version** — Splunk supports ARM
for the Universal Forwarder only. These Multipass VMs are arm64 (Apple Silicon) and
Multipass offers no x86_64 option there. Verified: UF arm64 → 200, Enterprise arm64
`.deb`/`.tgz` → 404 (10.4.2 and 9.3.2), Enterprise amd64 → 200 as a control.

The gate did its job — it fired before any role code was written.

**Decision (human).** Replace the addon with an ARM-native log stack that delivers the
same capability: an indexer tier, a search API, a dashboards UI, and log shipping from
every other VM. OpenSearch has real arm64 builds (3.4.0 and 2.18.0 both confirmed), as
does Fluent Bit.

**What changes.** The addon is renamed `splunk` → `opensearch` throughout: the `ADDONS`
tuple, `ADDON_NODE_ROLES`, the tfvars sizing keys, the Terraform locals and variables,
the inventory group, the `site.yml` play, the role directory, and `verify.py`'s branch
and parsers. Task 12's `search_peers_up` / `stats_count` are superseded by
`cluster_nodes` / `doc_count`, which read OpenSearch's JSON API.

**What gets simpler.** OpenSearch exposes an HTTP API, so verification reuses the
existing `_http` helper rather than SSH — and with the security plugin disabled (normal
for a local lab) there is no admin password at all. That removes the `opensearch` entry
from `SECRET_KEYS`, and with it the whole class of argv-leak and
credential-desynchronisation problems that cost three fix rounds on Keycloak and one on
Splunk's verifier. Keycloak still exercises the credentials path.

**Topology is unchanged:** three VMs. `opensearch-1` runs an OpenSearch node plus
Dashboards; `opensearch-2` and `opensearch-3` are additional cluster nodes. Fluent Bit
runs on every non-opensearch VM and ships `/var/log` into the cluster.

**Verification contract:** `GET /_cluster/health` must report `number_of_nodes == 3` and
a status of `green` or `yellow`, AND a Fluent-Bit-populated index must return a document
count greater than zero. Both gates, as before — a healthy cluster with no data flowing
does not pass.
