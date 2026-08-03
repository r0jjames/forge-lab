"""Orchestration tests for the teardown order, with every external call stubbed."""

import pytest

import deprovision
from forgelab.multipass import Node
from forgelab.proc import LabError


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Stub terraform/multipass and redirect generated files into tmp_path."""
    calls = []

    class FakeTerraform:
        workspace_exists = True

        def init(self):
            calls.append(("init",))

        def workspace_select(self, name):
            calls.append(("select", name))
            return self.workspace_exists

        def destroy(self, *args):
            calls.append(("destroy", *args))

        def workspace_delete(self, name):
            calls.append(("workspace_delete", name))

    class FakeMultipass:
        vms = []

        def list_vms(self, prefix=""):
            return [n for n in self.vms if n.name.startswith(prefix)]

        def delete_purge(self, names):
            calls.append(("delete_purge", tuple(names)))

    fake_tf, fake_mp = FakeTerraform(), FakeMultipass()
    inv_dir, ssh_dir = tmp_path / "inventory", tmp_path / "ssh_config.d"
    inv_dir.mkdir()
    ssh_dir.mkdir()

    monkeypatch.setattr(deprovision, "terraform", fake_tf)
    monkeypatch.setattr(deprovision, "multipass", fake_mp)
    monkeypatch.setattr(deprovision.proc, "require_tools", lambda *_: None)
    monkeypatch.setattr(deprovision.tfvars_mod, "resolve", lambda _: tmp_path / "x.tfvars")
    monkeypatch.setattr(deprovision.paths, "INV_DIR", inv_dir)
    monkeypatch.setattr(deprovision.sshconf.paths, "SSH_CONF_DIR", ssh_dir)

    return type(
        "Lab",
        (),
        {"calls": calls, "tf": fake_tf, "mp": fake_mp, "inv_dir": inv_dir, "ssh_dir": ssh_dir},
    )


def test_rejects_a_bad_cluster_name(lab):
    with pytest.raises(LabError, match=r"cluster_name must match"):
        deprovision.main(["Lab_1"])


def test_requires_a_cluster_name(lab):
    with pytest.raises(LabError, match="usage:"):
        deprovision.main([])


def test_destroys_then_deletes_the_workspace(lab):
    deprovision.main(["lab1"])
    assert lab.calls[0] == ("init",)
    assert lab.calls[1] == ("select", "lab1")
    assert lab.calls[2][0] == "destroy"
    assert lab.calls[3] == ("workspace_delete", "lab1")


def test_skips_destroy_when_no_workspace_exists(lab, capsys):
    lab.tf.workspace_exists = False
    deprovision.main(["lab1"])
    assert [c[0] for c in lab.calls] == ["init", "select"]
    assert "skipping destroy" in capsys.readouterr().out


def test_sweeps_leftover_vms_after_terraform(lab):
    lab.mp.vms = [Node("lab1-mgmt-1", []), Node("other-mgmt-1", [])]
    deprovision.main(["lab1"])
    assert ("delete_purge", ("lab1-mgmt-1",)) in lab.calls


def test_does_not_sweep_when_the_backend_is_clean(lab):
    deprovision.main(["lab1"])
    assert not [c for c in lab.calls if c[0] == "delete_purge"]


def test_removes_the_generated_inventory_and_ssh_config(lab):
    (lab.inv_dir / "lab1.ini").write_text("[mgmt]\n")
    (lab.ssh_dir / "lab1.conf").write_text("Host lab1-mgmt-1\n")
    deprovision.main(["lab1"])
    assert not (lab.inv_dir / "lab1.ini").exists()
    assert not (lab.ssh_dir / "lab1.conf").exists()


def test_tolerates_already_missing_generated_files(lab):
    deprovision.main(["lab1"])
