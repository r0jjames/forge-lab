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
