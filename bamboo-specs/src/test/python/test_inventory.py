import pytest

from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

MGMT = [Node("lab1-mgmt-1", ["192.168.252.10", "10.244.0.1"])]
COMPUTE = [
    Node("lab1-compute-1", ["192.168.252.11"]),
    Node("lab1-compute-2", ["192.168.252.12"]),
]
DATA = [
    Node("lab1-data-1", ["192.168.252.21"]),
    Node("lab1-data-2", ["192.168.252.22"]),
]
OPENSEARCH = [Node("lab1-opensearch-1", ["192.168.252.31"])]


def bare(cluster="lab1"):
    """A cluster with no addons: the data and opensearch groups are empty."""
    return {"mgmt": MGMT, "compute": COMPUTE, "data": [], "opensearch": []}


def loaded():
    return {"mgmt": MGMT, "compute": COMPUTE, "data": DATA, "opensearch": OPENSEARCH}


def test_render_produces_the_expected_inventory():
    assert inventory.render("lab1", bare()) == (
        "[mgmt]\n"
        "lab1-mgmt-1 ansible_host=192.168.252.10\n"
        "\n"
        "[compute]\n"
        "lab1-compute-1 ansible_host=192.168.252.11\n"
        "lab1-compute-2 ansible_host=192.168.252.12\n"
        "\n"
        "[data]\n"
        "\n"
        "[opensearch]\n"
        "\n"
        "[k8s_nodes:children]\n"
        "mgmt\n"
        "compute\n"
        "\n"
        "[all:vars]\n"
        "ansible_user=ubuntu\n"
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519\n"
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'\n"
        "cluster_name=lab1\n"
    )


def test_render_emits_empty_groups_so_plays_resolve_to_zero_hosts():
    """A `hosts: data` play must find an empty group, not an unknown one."""
    text = inventory.render("lab1", bare())
    assert "[data]\n" in text
    assert "[opensearch]\n" in text


def test_render_uses_the_lan_address_not_the_pod_address():
    assert "10.244.0.1" not in inventory.render("lab1", bare())


def test_parse_hosts_reads_every_group():
    assert inventory.parse_hosts(inventory.render("lab1", loaded())) == [
        ("lab1-mgmt-1", "192.168.252.10"),
        ("lab1-compute-1", "192.168.252.11"),
        ("lab1-compute-2", "192.168.252.12"),
        ("lab1-data-1", "192.168.252.21"),
        ("lab1-data-2", "192.168.252.22"),
        ("lab1-opensearch-1", "192.168.252.31"),
    ]


def test_parse_hosts_ignores_group_headers_and_vars():
    assert inventory.parse_hosts("[mgmt]\n\n[all:vars]\nansible_user=ubuntu\n") == []


def test_parse_hosts_ignores_the_children_group_members():
    """`mgmt` and `compute` under [k8s_nodes:children] are names, not hosts."""
    hosts = inventory.parse_hosts(inventory.render("lab1", bare()))
    assert ("mgmt", "") not in hosts
    assert len(hosts) == 3


def test_find_duplicate_ips_reports_the_mac_race():
    text = (
        "[mgmt]\na ansible_host=1.2.3.4\n"
        "[compute]\nb ansible_host=1.2.3.4\nc ansible_host=1.2.3.5\n"
    )
    assert inventory.find_duplicate_ips(text) == ["1.2.3.4"]


def test_find_duplicate_ips_is_empty_for_a_healthy_cluster():
    assert inventory.find_duplicate_ips(inventory.render("lab1", loaded())) == []


def test_first_ip_returns_the_first_host_of_the_named_group():
    text = inventory.render("lab1", loaded())
    assert inventory.first_ip(text, "data") == "192.168.252.21"
    assert inventory.first_ip(text, "opensearch") == "192.168.252.31"


def test_first_ip_is_empty_for_an_empty_or_absent_group():
    text = inventory.render("lab1", bare())
    assert inventory.first_ip(text, "data") == ""
    assert inventory.first_ip(text, "nosuchgroup") == ""


def test_group_ips_returns_every_host_of_the_group_in_order():
    assert inventory.group_ips(inventory.render("lab1", loaded()), "data") == [
        "192.168.252.21",
        "192.168.252.22",
    ]


def test_group_ips_is_empty_for_an_empty_group():
    assert inventory.group_ips(inventory.render("lab1", bare()), "opensearch") == []


def test_mgmt_ip_returns_the_first_mgmt_host():
    assert inventory.mgmt_ip(inventory.render("lab1", bare())) == "192.168.252.10"


def test_mgmt_ip_does_not_leak_a_compute_host():
    text = "[mgmt]\n\n[compute]\nc1 ansible_host=1.2.3.4\n"
    assert inventory.mgmt_ip(text) == ""


def test_assert_unique_ips_passes_on_a_healthy_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text(inventory.render("lab1", loaded()))
    inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_an_empty_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[mgmt]\n\n[compute]\n")
    with pytest.raises(LabError, match="has no hosts"):
        inventory.assert_unique_ips(inv)


def test_assert_unique_ips_rejects_duplicates(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text("[mgmt]\na ansible_host=1.2.3.4\nb ansible_host=1.2.3.4\n")
    with pytest.raises(LabError, match=r"duplicate node IP\(s\) \[1.2.3.4\]"):
        inventory.assert_unique_ips(inv)


def test_render_sorts_nodes_within_a_group_regardless_of_input_order():
    """multipass hands nodes back in an arbitrary order; groups['data'][0]
    (e.g. where the NameNode goes) must be a deterministic function of the
    name, not of backend enumeration order."""
    scrambled = {
        "mgmt": MGMT,
        "compute": COMPUTE,
        "data": [
            Node("lab1-data-3", ["192.168.252.23"]),
            Node("lab1-data-1", ["192.168.252.21"]),
            Node("lab1-data-2", ["192.168.252.22"]),
        ],
        "opensearch": [
            Node("lab1-opensearch-2", ["192.168.252.32"]),
            Node("lab1-opensearch-1", ["192.168.252.31"]),
        ],
    }
    text = inventory.render("lab1", scrambled)
    assert inventory.group_ips(text, "data") == [
        "192.168.252.21",
        "192.168.252.22",
        "192.168.252.23",
    ]
    assert inventory.group_ips(text, "opensearch") == [
        "192.168.252.31",
        "192.168.252.32",
    ]


def test_render_natural_sorts_double_digit_node_numbers():
    """Plain string sort would put '-data-10' before '-data-2'."""
    nodes = {
        "mgmt": MGMT,
        "compute": COMPUTE,
        "data": [
            Node("lab1-data-10", ["192.168.252.30"]),
            Node("lab1-data-2", ["192.168.252.22"]),
            Node("lab1-data-1", ["192.168.252.21"]),
        ],
        "opensearch": [],
    }
    text = inventory.render("lab1", nodes)
    assert inventory.parse_hosts(text)[3:6] == [
        ("lab1-data-1", "192.168.252.21"),
        ("lab1-data-2", "192.168.252.22"),
        ("lab1-data-10", "192.168.252.30"),
    ]


def test_render_preserves_the_callers_group_order():
    """Sorting is within a group only; group order itself is untouched — it
    is the caller's dict order (mgmt, compute, data, opensearch in provision.py)."""
    text = inventory.render("lab1", loaded())
    headers = [line for line in text.splitlines() if line.startswith("[")]
    assert headers == ["[mgmt]", "[compute]", "[data]", "[opensearch]", "[k8s_nodes:children]", "[all:vars]"]
