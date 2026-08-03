import pytest

from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

MGMT = [Node("lab1-mgmt-1", ["192.168.252.10", "10.244.0.1"])]
COMPUTE = [
    Node("lab1-compute-1", ["192.168.252.11"]),
    Node("lab1-compute-2", ["192.168.252.12"]),
]


def test_render_produces_the_expected_inventory():
    assert inventory.render("lab1", MGMT, COMPUTE) == (
        "[mgmt]\n"
        "lab1-mgmt-1 ansible_host=192.168.252.10\n"
        "\n"
        "[compute]\n"
        "lab1-compute-1 ansible_host=192.168.252.11\n"
        "lab1-compute-2 ansible_host=192.168.252.12\n"
        "\n"
        "[all:vars]\n"
        "ansible_user=ubuntu\n"
        "ansible_ssh_private_key_file=~/.forgelab/id_ed25519\n"
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'\n"
        "cluster_name=lab1\n"
    )


def test_render_uses_the_lan_address_not_the_pod_address():
    assert "10.244.0.1" not in inventory.render("lab1", MGMT, COMPUTE)


def test_parse_hosts_reads_every_group():
    hosts = inventory.parse_hosts(inventory.render("lab1", MGMT, COMPUTE))
    assert hosts == [
        ("lab1-mgmt-1", "192.168.252.10"),
        ("lab1-compute-1", "192.168.252.11"),
        ("lab1-compute-2", "192.168.252.12"),
    ]


def test_parse_hosts_ignores_group_headers_and_vars():
    assert inventory.parse_hosts("[mgmt]\n\n[all:vars]\nansible_user=ubuntu\n") == []


def test_find_duplicate_ips_reports_the_mac_race():
    text = (
        "[mgmt]\na ansible_host=1.2.3.4\n"
        "[compute]\nb ansible_host=1.2.3.4\nc ansible_host=1.2.3.5\n"
    )
    assert inventory.find_duplicate_ips(text) == ["1.2.3.4"]


def test_find_duplicate_ips_is_empty_for_a_healthy_cluster():
    assert inventory.find_duplicate_ips(inventory.render("lab1", MGMT, COMPUTE)) == []


def test_mgmt_ip_returns_the_first_mgmt_host():
    assert inventory.mgmt_ip(inventory.render("lab1", MGMT, COMPUTE)) == "192.168.252.10"


def test_mgmt_ip_does_not_leak_a_compute_host():
    text = "[mgmt]\n\n[compute]\nc1 ansible_host=1.2.3.4\n"
    assert inventory.mgmt_ip(text) == ""


def test_assert_unique_ips_passes_on_a_healthy_inventory(tmp_path):
    inv = tmp_path / "lab1.ini"
    inv.write_text(inventory.render("lab1", MGMT, COMPUTE))
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
