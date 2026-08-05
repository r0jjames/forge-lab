"""The PROV plan's Validate stage: what it reports and what it rejects."""

import pytest

import validate_prov as validate
from forgelab import planvars
from forgelab.proc import LabError

TFVARS = 'cluster_type = "k8s"\naddons = "hdfs"\n'


def report(tmp_path, name="lab1.tfvars", type_override="", addons_override=""):
    path = tmp_path / name
    path.write_text(TFVARS)
    cluster_type = planvars.resolve_cluster_type(type_override, TFVARS, str(path))
    addons = planvars.resolve_addons(addons_override, TFVARS, str(path))
    return validate.report(
        "lab1", cluster_type, addons, path, type_override, addons_override
    )


def test_reports_every_resolved_variable(tmp_path):
    lines = report(tmp_path)
    assert lines[0].startswith("==> cluster_name lab1")
    assert "k8s" in lines[1]
    assert "hdfs" in lines[2]
    assert "lab1.tfvars" in lines[3]


def test_credits_the_tfvars_file_when_nothing_was_overridden(tmp_path):
    lines = report(tmp_path)
    assert "from lab1.tfvars" in lines[1]


def test_credits_the_plan_variable_when_overridden(tmp_path):
    lines = report(tmp_path, type_override="dcos")
    assert "from the cluster_type plan variable" in lines[1]


def test_credits_the_tfvars_file_when_left_at_the_placeholder(tmp_path):
    lines = report(tmp_path, type_override=planvars.PLACEHOLDER_TYPE)
    assert "from lab1.tfvars" in lines[1]


def test_reports_no_addons_as_none(tmp_path):
    lines = report(tmp_path, addons_override="none")
    assert "==> addons       none" in lines[2]


def test_warns_when_the_cluster_has_no_tfvars_of_its_own(tmp_path):
    lines = report(tmp_path, name="defaults.tfvars")
    assert lines[0].startswith("WARNING: no clusters/lab1.tfvars")


def test_no_warning_when_the_cluster_has_its_own_tfvars(tmp_path):
    assert not any(line.startswith("WARNING") for line in report(tmp_path))


def test_main_rejects_a_bad_addon(clusters_dir):
    (clusters_dir / "lab1.tfvars").write_text(TFVARS)
    with pytest.raises(LabError, match=r"unknown addon\(s\) \[splunk\]"):
        validate.main(["lab1", "", "splunk"])


def test_main_accepts_the_shipped_defaults(clusters_dir, capsys):
    (clusters_dir / "lab1.tfvars").write_text(TFVARS)
    validate.main(["lab1", planvars.PLACEHOLDER_TYPE, planvars.PLACEHOLDER_ADDONS])
    assert "==> cluster_type k8s" in capsys.readouterr().out
