"""Validation gates and stage order for provision, with externals stubbed."""

import json
from pathlib import Path

import pytest

import provision
from forgelab import planvars
from forgelab.multipass import Node
from forgelab.proc import LabError


@pytest.fixture
def lab(tmp_path, monkeypatch):
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
            # rendered from backend state afterwards.
            existing["vms"] = [
                Node("lab1-mgmt-1", ["192.168.252.10"]),
                Node("lab1-compute-1", ["192.168.252.11"]),
            ]

    tfvars = tmp_path / "lab1.tfvars"
    tfvars.write_text('cluster_type  = "k8s"\nmgmt_mem      = "4G"\n')
    inv_dir = tmp_path / "inventory"
    registry_dir = tmp_path / "cluster_registered"

    monkeypatch.setattr(provision, "multipass", FakeMultipass())
    monkeypatch.setattr(provision, "terraform", FakeTerraform())
    monkeypatch.setattr(provision.proc, "require_tools", lambda *_: None)
    monkeypatch.setattr(provision.tfvars_mod, "resolve", lambda _: tfvars)
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
            "tfvars": tfvars,
            "inv_dir": inv_dir,
            "registry_dir": registry_dir,
        },
    )


def test_requires_a_cluster_name(lab):
    with pytest.raises(LabError, match="usage:"):
        provision.main([])


def test_rejects_an_uppercase_cluster_name(lab):
    with pytest.raises(LabError, match=r"cluster_name must match"):
        provision.main(["Lab1"])


def test_rejects_an_underscore_cluster_name(lab):
    with pytest.raises(LabError, match=r"cluster_name must match"):
        provision.main(["lab_1"])


def test_refuses_to_provision_over_existing_vms(lab):
    lab.existing["vms"] = [Node("lab1-mgmt-1", ["1.2.3.4"])]
    with pytest.raises(LabError, match="already exist; deprovision first"):
        provision.main(["lab1"])


def test_rejects_an_unknown_type_override(lab):
    with pytest.raises(LabError, match="got 'swarm' from the TYPE override"):
        provision.main(["lab1", "swarm"])


def test_rejects_an_unknown_type_from_the_tfvars_file(lab):
    lab.tfvars.write_text('cluster_type  = "swarm"\n')
    with pytest.raises(LabError, match=r"got 'swarm' from .*lab1\.tfvars"):
        provision.main(["lab1"])


def test_empty_type_argument_falls_back_to_the_tfvars_file(lab):
    """Bamboo always passes ${bamboo.cluster_type}, which may be empty."""
    provision.main(["lab1", ""])
    assert ("run", "ansible-playbook") in lab.calls


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
    assert kinds.index("registry") > len(kinds) - 2
    assert kinds[-1] == "registry"


def test_writes_the_cluster_info_file(lab):
    provision.main(["lab1"])
    info = (lab.registry_dir / "lab1_cluster_info.yml").read_text()
    assert "cluster: lab1" in info
    assert "ip: 192.168.252.10" in info
    assert "mem: 4G" in info


def test_leaves_no_cluster_info_when_verify_fails(lab, monkeypatch):
    def fail_on_verify(*args, **kwargs):
        calls = [str(a) for a in args]
        if calls[0] != "ansible-playbook":
            raise provision.proc.LabError("nodes not all Ready within timeout")

    monkeypatch.setattr(provision.proc, "run", fail_on_verify)
    with pytest.raises(provision.proc.LabError):
        provision.main(["lab1"])
    assert not (lab.registry_dir / "lab1_cluster_info.yml").exists()


def _capture_extra_vars(monkeypatch):
    """Patch install's ansible-playbook call and return the payload the
    caller's fake `run` records — read from the @varsfile JSON while the file
    still exists, since install.run() deletes it in a `finally` block.

    provision.install.proc and provision.proc are the same forgelab.proc
    module object, so this fake also receives Stage 4's verify.py call; it
    only has a "@varsfile" arg on the ansible-playbook call, so anything else
    is left alone (a no-op stand-in, same as the rest of the suite's fakes).
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


def test_passes_the_resolved_cluster_type_to_ansible(lab, monkeypatch):
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1", "dcos"])
    assert seen["payload"]["cluster_type"] == "dcos"


def test_validates_before_touching_the_backend(lab, monkeypatch):
    """A bad plan variable must not cost a multipass round trip.

    The Validate stage catches this earlier still, but `make provision` has no
    stages — this ordering is what makes the CLI fail as fast as the plan.
    """

    def explode(*_args, **_kwargs):
        raise AssertionError("multipass was queried before validation finished")

    monkeypatch.setattr(provision.multipass, "list_vms", explode)
    with pytest.raises(LabError, match=r"unknown addon\(s\) \[kafka\]"):
        provision.main(["lab1", "", "kafka"])


def test_placeholder_addons_fall_back_to_the_tfvars_file(lab, monkeypatch):
    """The shipped default is a menu, not a request for those three addons."""
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs"\n')
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1", planvars.PLACEHOLDER_TYPE, planvars.PLACEHOLDER_ADDONS])
    assert seen["payload"]["addons"] == "hdfs"
    assert seen["payload"]["cluster_type"] == "k8s"


def test_none_disables_every_addon(lab, monkeypatch):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs,keycloak"\n')
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1", "", "none"])
    assert seen["payload"]["addons"] == ""


def test_passes_the_resolved_addons_to_ansible(lab, monkeypatch):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs"\n')
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["addons"] == "hdfs"


def test_addons_argument_overrides_the_tfvars_file(lab, monkeypatch):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs"\n')
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1", "", "keycloak"])
    assert seen["payload"]["addons"] == "keycloak"


def test_node_count_overrides_zeroes_every_role_when_no_addons():
    assert provision.node_count_overrides([]) == [
        "-var", "data_count=0", "-var", "opensearch_count=0",
    ]


def test_node_count_overrides_leaves_an_enabled_role_alone():
    assert provision.node_count_overrides(["hdfs"]) == ["-var", "opensearch_count=0"]


def test_node_count_overrides_is_empty_when_every_role_is_wanted():
    assert provision.node_count_overrides(["hdfs", "opensearch", "keycloak"]) == []


def test_keycloak_alone_builds_no_extra_vms():
    """Keycloak runs on the k8s cluster; it needs no VM role of its own."""
    assert provision.node_count_overrides(["keycloak"]) == [
        "-var", "data_count=0", "-var", "opensearch_count=0",
    ]


def test_apply_zeroes_the_vm_roles_of_disabled_addons(lab):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = ""\n')
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert "data_count=0" in apply
    assert "opensearch_count=0" in apply


def test_apply_keeps_the_vm_roles_of_enabled_addons(lab):
    lab.tfvars.write_text('cluster_type = "k8s"\naddons = "hdfs,opensearch"\n')
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert "data_count=0" not in apply
    assert "opensearch_count=0" not in apply
