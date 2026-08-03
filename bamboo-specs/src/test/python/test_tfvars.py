import pytest

from forgelab import paths
from forgelab import tfvars as tfvars_mod
from forgelab.proc import LabError


@pytest.fixture
def clusters_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CLUSTERS_DIR", tmp_path)
    return tmp_path


def test_resolve_prefers_the_named_cluster(clusters_dir):
    (clusters_dir / "lab1.tfvars").write_text("")
    (clusters_dir / "defaults.tfvars").write_text("")
    assert tfvars_mod.resolve("lab1").name == "lab1.tfvars"


def test_resolve_falls_back_to_defaults(clusters_dir):
    (clusters_dir / "defaults.tfvars").write_text("")
    assert tfvars_mod.resolve("nope").name == "defaults.tfvars"


def test_resolve_fails_when_nothing_exists(clusters_dir):
    with pytest.raises(LabError, match="no tfvars found"):
        tfvars_mod.resolve("lab1")


def test_parse_cluster_type_reads_the_quoted_value():
    text = 'cluster_type  = "dcos"\nmgmt_count    = 1\n'
    assert tfvars_mod.parse_cluster_type(text) == "dcos"


def test_parse_cluster_type_ignores_other_keys():
    assert tfvars_mod.parse_cluster_type('mgmt_mem = "4G"\n') == ""


def test_parse_cluster_type_is_empty_when_unquoted():
    assert tfvars_mod.parse_cluster_type("cluster_type = k8s\n") == ""
