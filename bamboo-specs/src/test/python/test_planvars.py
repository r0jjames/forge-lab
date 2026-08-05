"""Plan variables: placeholders, the `none` keyword, and what each rejects."""

import pytest

from forgelab import planvars
from forgelab.proc import LabError

TFVARS = 'cluster_type = "k8s"\naddons = "hdfs,opensearch"\n'


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


def test_names_the_offending_value(name="Lab1"):
    with pytest.raises(LabError, match="got 'Lab1'"):
        planvars.require_cluster_name(name, "usage")


# --- cluster_type ---------------------------------------------------------


def test_type_override_beats_the_file():
    assert planvars.resolve_cluster_type("dcos", TFVARS, "f.tfvars") == "dcos"


def test_empty_type_falls_back_to_the_file():
    assert planvars.resolve_cluster_type("", TFVARS, "f.tfvars") == "k8s"


def test_type_placeholder_falls_back_to_the_file():
    assert planvars.resolve_cluster_type(
        planvars.PLACEHOLDER_TYPE, TFVARS, "f.tfvars"
    ) == "k8s"


def test_a_near_miss_placeholder_is_not_a_sentinel():
    """`k8s|dcos` is a typo of the menu, not a way to say "unset"."""
    with pytest.raises(LabError, match=r"got 'k8s\|dcos'"):
        planvars.resolve_cluster_type("k8s|dcos", TFVARS, "f.tfvars")


def test_a_menu_shaped_value_says_to_pick_one():
    with pytest.raises(LabError, match="that is the menu, not a value"):
        planvars.resolve_cluster_type("k8s|dcos", TFVARS, "f.tfvars")


def test_rejects_an_unknown_type_naming_the_source():
    with pytest.raises(LabError, match="got 'swarm' from the TYPE override"):
        planvars.resolve_cluster_type("swarm", TFVARS, "f.tfvars")


def test_rejects_an_unknown_type_from_the_file_naming_the_file():
    with pytest.raises(LabError, match="got 'swarm' from f.tfvars"):
        planvars.resolve_cluster_type("", 'cluster_type = "swarm"\n', "f.tfvars")


def test_type_error_lists_the_legal_values():
    with pytest.raises(LabError, match=r"one of \[k8s dcos\]"):
        planvars.resolve_cluster_type("swarm", TFVARS, "f.tfvars")


# --- addons ---------------------------------------------------------------


def test_addons_override_beats_the_file():
    assert planvars.resolve_addons("keycloak", TFVARS, "f.tfvars") == ["keycloak"]


def test_empty_addons_fall_back_to_the_file():
    """Bamboo always passes ${bamboo.addons}, which may be empty."""
    assert planvars.resolve_addons("  ", TFVARS, "f.tfvars") == ["hdfs", "opensearch"]


def test_addons_placeholder_falls_back_to_the_file():
    assert planvars.resolve_addons(
        planvars.PLACEHOLDER_ADDONS, TFVARS, "f.tfvars"
    ) == ["hdfs", "opensearch"]


def test_none_means_no_addons():
    assert planvars.resolve_addons("none", TFVARS, "f.tfvars") == []


def test_none_from_the_tfvars_file_also_means_no_addons():
    assert planvars.resolve_addons("", 'addons = "none"\n', "f.tfvars") == []


def test_none_cannot_be_combined_with_a_real_addon():
    with pytest.raises(LabError, match=r"'none' cannot be combined with \[hdfs\]"):
        planvars.resolve_addons("none,hdfs", TFVARS, "f.tfvars")


def test_rejects_an_unknown_addon_naming_the_source():
    with pytest.raises(LabError, match="from the ADDONS override"):
        planvars.resolve_addons("kafka", TFVARS, "f.tfvars")


def test_rejects_an_unknown_addon_from_the_file_naming_the_file():
    with pytest.raises(LabError, match=r"unknown addon\(s\) \[kafka\] from f.tfvars"):
        planvars.resolve_addons("", 'addons = "kafka"\n', "f.tfvars")


def test_addon_error_lists_the_known_names_including_none():
    with pytest.raises(LabError, match="known: keycloak hdfs opensearch none"):
        planvars.resolve_addons("kafka", TFVARS, "f.tfvars")


def test_splunk_is_not_an_addon():
    """OpenSearch replaced it: Splunk Enterprise has no Linux arm64 build."""
    with pytest.raises(LabError, match=r"unknown addon\(s\) \[splunk\]"):
        planvars.resolve_addons("splunk", TFVARS, "f.tfvars")


def test_addons_tolerate_spaces_and_trailing_commas():
    assert planvars.resolve_addons(" hdfs , keycloak ,", TFVARS, "f.tfvars") == [
        "hdfs",
        "keycloak",
    ]


def test_addons_are_deduplicated_in_the_order_given():
    assert planvars.resolve_addons(
        "opensearch,hdfs,opensearch", TFVARS, "f.tfvars"
    ) == ["opensearch", "hdfs"]


# --- resolve() ------------------------------------------------------------


def test_resolve_returns_every_value_and_the_sizing_file(clusters_dir):
    (clusters_dir / "lab1.tfvars").write_text(TFVARS)
    cluster, cluster_type, addons, path = planvars.resolve("lab1", "", "", "usage")
    assert (cluster, cluster_type, addons) == ("lab1", "k8s", ["hdfs", "opensearch"])
    assert path.name == "lab1.tfvars"


def test_resolve_checks_the_name_before_reading_any_file(clusters_dir):
    """No tfvars exist here — a bad name must fail on the name, not the read."""
    with pytest.raises(LabError, match="cluster_name must match"):
        planvars.resolve("Lab1", "", "", "usage")
