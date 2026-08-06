"""The strict YAML subset: what it accepts, and what it refuses by name."""

import pytest

from forgelab import clusterconfig
from forgelab.proc import LabError


def test_parses_nested_mappings():
    text = (
        "cluster:\n"
        "  type: k8s\n"
        "cluster_nodes:\n"
        "  management:\n"
        "    count: 1\n"
        "    memory: 4G\n"
    )
    assert clusterconfig.parse(text, "c.yaml") == {
        "cluster": {"type": "k8s"},
        "cluster_nodes": {"management": {"count": "1", "memory": "4G"}},
    }


def test_ignores_comments_and_blank_lines():
    text = "# sizing\n\ncluster:\n  type: k8s  # the default\n"
    assert clusterconfig.parse(text, "c.yaml") == {"cluster": {"type": "k8s"}}


def test_a_bare_hash_inside_a_value_is_part_of_the_value():
    """Real YAML opens a comment only after whitespace. Splitting on any '#'
    would silently truncate the value instead of rejecting it."""
    assert clusterconfig.parse("a: v1#2\n", "c.yaml") == {"a": "v1#2"}


def test_a_hash_inside_a_quoted_value_survives():
    assert clusterconfig.parse('a: "b#c"\n', "c.yaml") == {"a": "b#c"}


def test_a_comment_after_whitespace_is_still_stripped():
    assert clusterconfig.parse("a: 4G  # bump me\n", "c.yaml") == {"a": "4G"}


def test_a_comment_directly_after_a_key_opener_is_stripped():
    assert clusterconfig.parse("a:  # a block\n  b: 1\n", "c.yaml") == {"a": {"b": "1"}}


def test_dedents_back_to_an_outer_mapping():
    text = "a:\n  b:\n    c: 1\nd: 2\n"
    assert clusterconfig.parse(text, "c.yaml") == {"a": {"b": {"c": "1"}}, "d": "2"}


def test_strips_matching_quotes_from_a_value():
    assert clusterconfig.parse('a: "4G"\n', "c.yaml") == {"a": "4G"}
    assert clusterconfig.parse("a: '4G'\n", "c.yaml") == {"a": "4G"}


@pytest.mark.parametrize(
    "text,message",
    [
        ("a:\n\tb: 1\n", "tabs are not allowed"),
        ("a:\n  - one\n", "sequences are not supported"),
        ("a: {b: 1}\n", "flow collections are not supported"),
        ("a: [1, 2]\n", "flow collections are not supported"),
        ("a: &anchor\n", "anchors and aliases are not supported"),
        ("a: *anchor\n", "anchors and aliases are not supported"),
        ("---\na: 1\n", "document markers are not supported"),
        ("a:\n   b: 1\n", "not a multiple of two"),
        ("a:\n    b: 1\n", "jumps more than one level"),
        ("just_words\n", "expected 'key: value'"),
        ("a: 1\na: 2\n", "duplicate key 'a'"),
    ],
)
def test_rejects_everything_outside_the_subset(text, message):
    with pytest.raises(LabError, match=message):
        clusterconfig.parse(text, "c.yaml")


def test_errors_name_the_file_and_the_line():
    with pytest.raises(LabError, match=r"c\.yaml:3:"):
        clusterconfig.parse("a:\n  b: 1\n  - two\n", "c.yaml")


CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G
  opensearch:
    enabled: false
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
  keycloak:
    enabled: true
"""


def config(text=CONFIG):
    return clusterconfig.from_text(text, "lab1_cluster.yaml")


def without(section):
    """CONFIG with one whole top-level section removed."""
    kept, dropping = [], False
    for line in CONFIG.splitlines():
        if line and not line.startswith(" "):
            dropping = line.startswith(f"{section}:")
        if not dropping:
            kept.append(line)
    return "\n".join(kept) + "\n"


# --- accessors ------------------------------------------------------------


def test_reads_the_cluster_type():
    assert config().cluster_type == "k8s"


def test_enabled_lists_only_the_enabled_technologies_in_file_order():
    assert config().enabled() == ["hdfs", "keycloak"]


def test_roles_prefix_technology_nodes_and_leave_cluster_nodes_alone():
    assert [r.role for r in config().roles()] == [
        "management", "compute", "hdfs-namenode", "hdfs-datanode",
    ]


def test_groups_replace_dashes_with_underscores():
    assert [r.group for r in config().roles()] == [
        "management", "compute", "hdfs_namenode", "hdfs_datanode",
    ]


def test_a_disabled_technology_contributes_no_roles():
    assert not [r for r in config().roles() if "opensearch" in r.role]


def test_nodes_map_numbers_every_vm_from_one():
    assert config().nodes_map("lab1") == {
        "lab1-management-1": {"cpus": 2, "memory": "4G", "disk": "20G"},
        "lab1-compute-1": {"cpus": 2, "memory": "3G", "disk": "20G"},
        "lab1-compute-2": {"cpus": 2, "memory": "3G", "disk": "20G"},
        "lab1-hdfs-namenode-1": {"cpus": 2, "memory": "4G", "disk": "20G"},
        "lab1-hdfs-datanode-1": {"cpus": 2, "memory": "4G", "disk": "40G"},
        "lab1-hdfs-datanode-2": {"cpus": 2, "memory": "4G", "disk": "40G"},
        "lab1-hdfs-datanode-3": {"cpus": 2, "memory": "4G", "disk": "40G"},
    }


def test_a_zero_count_role_builds_no_vms():
    text = CONFIG.replace("    count: 2\n    cpu: 2\n    memory: 3G", "    count: 0\n    cpu: 2\n    memory: 3G")
    assert not [n for n in config(text).nodes_map("lab1") if "compute" in n]


def test_children_group_the_cluster_nodes_and_each_enabled_technology():
    assert config().children() == {
        "k8s_nodes": ["management", "compute"],
        "hdfs_nodes": ["hdfs_namenode", "hdfs_datanode"],
    }


def test_keycloak_gets_no_children_group_because_it_owns_no_vms():
    assert "keycloak_nodes" not in config().children()


def test_sizing_by_role_keys_on_the_dashed_role():
    assert config().sizing_by_role()["hdfs-datanode"] == {
        "cpu": "2", "mem": "4G", "disk": "40G",
    }


# --- validation -----------------------------------------------------------


def test_rejects_an_unknown_cluster_type():
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        config(CONFIG.replace("type: k8s", "type: swarm"))


def test_rejects_a_missing_cluster_section():
    with pytest.raises(LabError, match="missing required key 'cluster.type'"):
        config(without("cluster"))


def test_rejects_a_missing_cluster_nodes_section():
    with pytest.raises(LabError, match="missing required key 'cluster_nodes'"):
        config(without("cluster_nodes"))


def test_requires_a_management_role():
    with pytest.raises(LabError, match="cluster_nodes must define 'management'"):
        config(CONFIG.replace("  management:", "  control:"))


def test_requires_at_least_one_management_node():
    text = CONFIG.replace(
        "  management:\n    count: 1", "  management:\n    count: 0"
    )
    with pytest.raises(LabError, match="cluster_nodes.management.count must be at least 1"):
        config(text)


def test_rejects_a_missing_node_field():
    text = CONFIG.replace("  compute:\n    count: 2\n    cpu: 2\n", "  compute:\n    count: 2\n")
    with pytest.raises(LabError, match="missing required key 'cluster_nodes.compute.cpu'"):
        config(text)


@pytest.mark.parametrize("bad", ["4Gi", "4", "4g", "big"])
def test_rejects_a_memory_value_multipass_would_not_take(bad):
    with pytest.raises(LabError, match=r"must look like 4G or 512M"):
        config(CONFIG.replace("memory: 3G", f"memory: {bad}"))


def test_rejects_a_non_numeric_count():
    with pytest.raises(LabError, match="must be a whole number"):
        config(CONFIG.replace("    count: 2", "    count: two"))


def test_rejects_a_cpu_below_one():
    with pytest.raises(LabError, match="cpu must be at least 1"):
        config(CONFIG.replace("  compute:\n    count: 2\n    cpu: 2", "  compute:\n    count: 2\n    cpu: 0"))


def test_rejects_an_unknown_technology():
    with pytest.raises(LabError, match=r"unknown technology 'kafka'; known: keycloak hdfs opensearch"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  kafka:\n    enabled: true"))


def test_rejects_a_node_key_the_technology_does_not_own():
    """A stray node key would build VMs no ansible role installs onto."""
    text = CONFIG.replace(
        "      datanode:\n        count: 3",
        "      standby:\n        count: 3",
    )
    with pytest.raises(
        LabError,
        match=r"unknown key 'technologies.hdfs.nodes.standby'; known: namenode datanode",
    ):
        config(text)


def test_rejects_a_misspelled_node_key():
    text = CONFIG.replace("      datanode:", "      datanodes:")
    with pytest.raises(LabError, match=r"unknown key 'technologies.hdfs.nodes.datanodes'"):
        config(text)


def test_opensearch_owns_only_a_master_node():
    text = CONFIG.replace(
        "  opensearch:\n    enabled: false", "  opensearch:\n    enabled: true"
    ).replace("      master:", "      data:")
    with pytest.raises(LabError, match=r"unknown key 'technologies.opensearch.nodes.data'"):
        config(text)


def test_a_disabled_technology_may_still_carry_a_stray_node_key():
    """Disabled blocks are parked, not validated — same as their sizing."""
    text = CONFIG.replace("      master:", "      data:")
    assert config(text).enabled() == ["hdfs", "keycloak"]


def test_rejects_a_technology_with_no_enabled_key():
    with pytest.raises(LabError, match="missing required key 'technologies.keycloak.enabled'"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  keycloak:\n    nodes:\n      x:\n        count: 1"))


def test_rejects_a_non_boolean_enabled():
    with pytest.raises(LabError, match="must be true or false"):
        config(CONFIG.replace("  keycloak:\n    enabled: true", "  keycloak:\n    enabled: yes"))


def test_hdfs_must_have_exactly_one_namenode():
    text = CONFIG.replace(
        "      namenode:\n        count: 1", "      namenode:\n        count: 2"
    )
    with pytest.raises(LabError, match="hdfs has exactly one NameNode"):
        config(text)


def test_keycloak_must_not_declare_nodes():
    text = CONFIG.replace(
        "  keycloak:\n    enabled: true",
        "  keycloak:\n    enabled: true\n    nodes:\n      web:\n        count: 1\n        cpu: 1\n        memory: 1G\n        disk: 5G",
    )
    with pytest.raises(LabError, match="keycloak runs on the cluster and declares no nodes"):
        config(text)


def test_an_enabled_technology_that_owns_vms_must_declare_nodes():
    text = CONFIG.replace(
        "  opensearch:\n    enabled: false", "  opensearch:\n    enabled: true"
    ).replace(
        "    nodes:\n      master:\n        count: 3\n        cpu: 2\n        memory: 6G\n        disk: 40G\n",
        "",
    )
    with pytest.raises(LabError, match="technologies.opensearch must declare nodes"):
        config(text)


def test_a_disabled_technology_keeps_its_unvalidated_sizing():
    """`enabled: false` is how you park a technology without retyping it."""
    text = CONFIG.replace("        memory: 6G", "        memory: nonsense")
    assert config(text).enabled() == ["hdfs", "keycloak"]


def test_rejects_an_unknown_key_at_any_level():
    with pytest.raises(LabError, match=r"unknown key 'cluster_nodes.compute.memmory'"):
        config(CONFIG.replace("    memory: 3G", "    memmory: 3G"))


def test_rejects_an_unknown_top_level_section():
    with pytest.raises(LabError, match="unknown key 'extras'"):
        config(CONFIG + "extras:\n  a: 1\n")


# --- load() ---------------------------------------------------------------


def test_load_reads_the_named_file(configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    assert clusterconfig.load("lab1").cluster_type == "k8s"


def test_load_names_the_missing_path_and_what_does_exist(configs_dir):
    (configs_dir / "lab2_cluster.yaml").write_text(CONFIG)
    with pytest.raises(LabError, match="no config at .*lab1_cluster.yaml.*available: lab2"):
        clusterconfig.load("lab1")


def test_load_says_the_directory_is_empty_when_it_is(configs_dir):
    with pytest.raises(LabError, match="available: none"):
        clusterconfig.load("lab1")


def test_every_committed_config_is_valid():
    """A broken config must not be able to reach main."""
    from forgelab import paths

    for path in sorted(paths.CLUSTER_CONFIGS_DIR.glob("*_cluster.yaml")):
        clusterconfig.from_text(path.read_text(), str(path))
