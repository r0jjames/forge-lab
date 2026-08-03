"""Validation gates and stage order for provision, with externals stubbed."""

import pytest

import provision
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


def test_tells_ansible_where_to_report_components(lab, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    provision.main(["lab1"])
    ansible = next(c for c in recorded if c[0] == "ansible-playbook")
    assert any(arg.startswith("component_report=") for arg in ansible)


def test_passes_the_resolved_cluster_type_to_ansible(lab, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    provision.main(["lab1", "dcos"])
    ansible = next(c for c in recorded if c[0] == "ansible-playbook")
    assert "cluster_type=dcos" in ansible
