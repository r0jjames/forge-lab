import pytest

from forgelab import tfvars as tfvars_mod
from forgelab.proc import LabError

# clusters_dir lives in conftest.py — test_planvars.py resolves tfvars too.


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


def test_parse_returns_every_key_unquoted():
    text = 'cluster_type  = "k8s"\nmgmt_count    = 1\nmgmt_mem      = "4G"\n'
    assert tfvars_mod.parse(text) == {
        "cluster_type": "k8s",
        "mgmt_count": "1",
        "mgmt_mem": "4G",
    }


def test_parse_ignores_comments_and_blank_lines():
    text = '# sizing\n\nmgmt_cpu = 2  # bump me\n'
    assert tfvars_mod.parse(text) == {"mgmt_cpu": "2"}


def test_parse_addons_splits_the_comma_list():
    text = 'addons = "keycloak,hdfs,opensearch"\n'
    assert tfvars_mod.parse_addons(text) == ["keycloak", "hdfs", "opensearch"]


def test_parse_addons_tolerates_spaces_and_trailing_commas():
    text = 'addons = "keycloak, hdfs ,"\n'
    assert tfvars_mod.parse_addons(text) == ["keycloak", "hdfs"]


def test_parse_addons_is_empty_when_the_value_is_empty():
    assert tfvars_mod.parse_addons('addons = ""\n') == []


def test_parse_addons_is_empty_when_the_key_is_absent():
    assert tfvars_mod.parse_addons('cluster_type = "k8s"\n') == []


def test_parse_addons_ignores_a_commented_line():
    assert tfvars_mod.parse_addons('# addons = "opensearch"\n') == []


def test_parse_still_reads_the_new_sizing_scalars():
    text = 'addons = "hdfs"\ndata_mem = "4G"\ndata_count = 3\n'
    parsed = tfvars_mod.parse(text)
    assert parsed["data_mem"] == "4G"
    assert parsed["data_count"] == "3"
