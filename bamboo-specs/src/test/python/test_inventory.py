import pytest

from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

CHILDREN = {
    "k8s_nodes": ["management", "compute"],
    "hdfs_nodes": ["hdfs_namenode", "hdfs_datanode"],
    "opensearch_nodes": ["opensearch_master"],
}

MANAGEMENT = [Node("lab1-management-1", ["192.168.252.10", "10.244.0.1"])]
COMPUTE = [
    Node("lab1-compute-1", ["192.168.252.11"]),
    Node("lab1-compute-2", ["192.168.252.12"]),
]
NAMENODE = [Node("lab1-hdfs-namenode-1", ["192.168.252.20"])]
DATANODE = [
    Node("lab1-hdfs-datanode-1", ["192.168.252.21"]),
    Node("lab1-hdfs-datanode-2", ["192.168.252.22"]),
]
OPENSEARCH = [Node("lab1-opensearch-master-1", ["192.168.252.31"])]


def bare(cluster="lab1"):
    """No addons: the namenode, datanode and opensearch groups are empty."""
    return {
        "management": MANAGEMENT,
        "compute": COMPUTE,
        "hdfs_namenode": [],
        "hdfs_datanode": [],
        "opensearch_master": [],
    }


def loaded():
    return {
        "management": MANAGEMENT,
        "compute": COMPUTE,
        "hdfs_namenode": NAMENODE,
        "hdfs_datanode": DATANODE,
        "opensearch_master": OPENSEARCH,
    }


def test_render_produces_the_expected_inventory():
    assert inventory.render("lab1", bare(), CHILDREN) == (
        "[management]\n"
        "lab1-management-1 ansible_host=192.168.252.10\n"
        "\n"
        "[compute]\n"
        "lab1-compute-1 ansible_host=192.168.252.11\n"
        "lab1-compute-2 ansible_host=192.168.252.12\n"
        "\n"
        "[hdfs_namenode]\n"
        "\n"
        "[hdfs_datanode]\n"
        "\n"
        "[opensearch_master]\n"
        "\n"
        "[k8s_nodes:children]\n"
        "management\n"
        "compute\n"
        "\n"
        "[hdfs_nodes:children]\n"
        "hdfs_namenode\n"
        "hdfs_datanode\n"
        "\n"
        "[opensearch_nodes:children]\n"
        "opensearch_master\n"
        "\n"
        "[all:vars]\n"
        "ansible_user=ubuntu\n"
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519\n"
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'\n"
        "cluster_name=lab1\n"
    )


def test_render_emits_only_the_children_it_is_given():
    """A cluster with no technologies has no <name>_nodes groups at all."""
    text = inventory.render("lab1", bare(), {"k8s_nodes": ["management", "compute"]})
    assert "[hdfs_nodes:children]" not in text
    assert "[k8s_nodes:children]" in text


def test_render_emits_empty_groups_so_plays_resolve_to_zero_hosts():
    """A `hosts: datanode` play must find an empty group, not an unknown one."""
    text = inventory.render("lab1", bare(), CHILDREN)
    assert "[hdfs_namenode]\n" in text
    assert "[hdfs_datanode]\n" in text
    assert "[opensearch_master]\n" in text


def test_render_uses_the_lan_address_not_the_pod_address():
    assert "10.244.0.1" not in inventory.render("lab1", bare(), CHILDREN)


def test_parse_hosts_reads_every_group():
    assert inventory.parse_hosts(inventory.render("lab1", loaded(), CHILDREN)) == [
        ("lab1-management-1", "192.168.252.10"),
        ("lab1-compute-1", "192.168.252.11"),
        ("lab1-compute-2", "192.168.252.12"),
        ("lab1-hdfs-namenode-1", "192.168.252.20"),
        ("lab1-hdfs-datanode-1", "192.168.252.21"),
        ("lab1-hdfs-datanode-2", "192.168.252.22"),
        ("lab1-opensearch-master-1", "192.168.252.31"),
    ]


def test_parse_hosts_ignores_group_headers_and_vars():
    assert inventory.parse_hosts("[management]\n\n[all:vars]\nansible_user=ubuntu\n") == []


def test_parse_hosts_ignores_the_children_group_members():
    """`management` and `compute` under [k8s_nodes:children] are names, not hosts."""
    hosts = inventory.parse_hosts(inventory.render("lab1", bare(), CHILDREN))
    assert ("management", "") not in hosts
    assert len(hosts) == 3


def test_find_duplicate_ips_reports_the_mac_race():
    text = (
        "[management]\na ansible_host=1.2.3.4\n"
        "[compute]\nb ansible_host=1.2.3.4\nc ansible_host=1.2.3.5\n"
    )
    assert inventory.find_duplicate_ips(text) == ["1.2.3.4"]


def test_find_duplicate_ips_is_empty_for_a_healthy_cluster():
    assert inventory.find_duplicate_ips(inventory.render("lab1", loaded(), CHILDREN)) == []


def test_first_ip_returns_the_first_host_of_the_named_group():
    text = inventory.render("lab1", loaded(), CHILDREN)
    assert inventory.first_ip(text, "hdfs_namenode") == "192.168.252.20"
    assert inventory.first_ip(text, "hdfs_datanode") == "192.168.252.21"
    assert inventory.first_ip(text, "opensearch_master") == "192.168.252.31"


def test_first_ip_is_empty_for_an_empty_or_absent_group():
    text = inventory.render("lab1", bare(), CHILDREN)
    assert inventory.first_ip(text, "hdfs_datanode") == ""
    assert inventory.first_ip(text, "nosuchgroup") == ""


def test_group_ips_returns_every_host_of_the_group_in_order():
    assert inventory.group_ips(
        inventory.render("lab1", loaded(), CHILDREN), "hdfs_datanode"
    ) == [
        "192.168.252.21",
        "192.168.252.22",
    ]


def test_group_ips_is_empty_for_an_empty_group():
    assert (
        inventory.group_ips(inventory.render("lab1", bare(), CHILDREN), "opensearch_master")
        == []
    )


def test_control_ip_returns_the_first_management_host():
    assert (
        inventory.control_ip(inventory.render("lab1", bare(), CHILDREN))
        == "192.168.252.10"
    )


def test_control_ip_does_not_leak_a_compute_host():
    text = "[management]\n\n[compute]\nc1 ansible_host=1.2.3.4\n"
    assert inventory.control_ip(text) == ""


def test_assert_unique_ips_passes_on_a_healthy_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text(inventory.render("lab1", loaded(), CHILDREN))
    inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_an_empty_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[management]\n\n[compute]\n")
    with pytest.raises(LabError, match="has no hosts"):
        inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_duplicates(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[management]\na ansible_host=1.2.3.4\nb ansible_host=1.2.3.4\n")
    with pytest.raises(LabError, match=r"duplicate node IP\(s\) \[1.2.3.4\]"):
        inventory.assert_unique_ips(inv)


def test_render_sorts_nodes_within_a_group_regardless_of_input_order():
    """multipass hands nodes back in an arbitrary order; the first host of a
    group (e.g. groups['opensearch_master'][0], which also runs Dashboards)
    must be a deterministic function of the name, not of backend enumeration
    order."""
    scrambled = {
        "management": MANAGEMENT,
        "compute": COMPUTE,
        "hdfs_namenode": NAMENODE,
        "hdfs_datanode": [
            Node("lab1-hdfs-datanode-3", ["192.168.252.23"]),
            Node("lab1-hdfs-datanode-1", ["192.168.252.21"]),
            Node("lab1-hdfs-datanode-2", ["192.168.252.22"]),
        ],
        "opensearch_master": [
            Node("lab1-opensearch-master-2", ["192.168.252.32"]),
            Node("lab1-opensearch-master-1", ["192.168.252.31"]),
        ],
    }
    text = inventory.render("lab1", scrambled, CHILDREN)
    assert inventory.group_ips(text, "hdfs_datanode") == [
        "192.168.252.21",
        "192.168.252.22",
        "192.168.252.23",
    ]
    assert inventory.group_ips(text, "opensearch_master") == [
        "192.168.252.31",
        "192.168.252.32",
    ]


def test_render_natural_sorts_double_digit_node_numbers():
    """Plain string sort would put '-datanode-10' before '-datanode-2'."""
    nodes = {
        "management": MANAGEMENT,
        "compute": COMPUTE,
        "hdfs_namenode": NAMENODE,
        "hdfs_datanode": [
            Node("lab1-hdfs-datanode-10", ["192.168.252.30"]),
            Node("lab1-hdfs-datanode-2", ["192.168.252.22"]),
            Node("lab1-hdfs-datanode-1", ["192.168.252.21"]),
        ],
        "opensearch_master": [],
    }
    text = inventory.render("lab1", nodes, CHILDREN)
    assert inventory.parse_hosts(text)[4:7] == [
        ("lab1-hdfs-datanode-1", "192.168.252.21"),
        ("lab1-hdfs-datanode-2", "192.168.252.22"),
        ("lab1-hdfs-datanode-10", "192.168.252.30"),
    ]


def test_render_preserves_the_callers_group_order():
    """Sorting is within a group only; group order itself is untouched — it
    is the caller's dict order (management, compute, namenode, datanode,
    opensearch in provision.py)."""
    text = inventory.render("lab1", loaded(), CHILDREN)
    headers = [line for line in text.splitlines() if line.startswith("[")]
    assert headers == [
        "[management]",
        "[compute]",
        "[hdfs_namenode]",
        "[hdfs_datanode]",
        "[opensearch_master]",
        "[k8s_nodes:children]",
        "[hdfs_nodes:children]",
        "[opensearch_nodes:children]",
        "[all:vars]",
    ]
