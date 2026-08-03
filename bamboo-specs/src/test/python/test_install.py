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
