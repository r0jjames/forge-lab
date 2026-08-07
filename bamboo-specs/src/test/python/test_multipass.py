import json

from forgelab import multipass

SAMPLE = json.dumps(
    {
        "list": [
            {"name": "lab1-management-1", "ipv4": ["192.168.252.10", "10.244.0.1"]},
            {"name": "lab1-compute-1", "ipv4": ["192.168.252.11"]},
            {"name": "other-management-1", "ipv4": ["192.168.252.12"]},
        ]
    }
)


def test_parse_list_filters_by_prefix():
    nodes = multipass.parse_list(SAMPLE, "lab1-")
    assert [n.name for n in nodes] == ["lab1-management-1", "lab1-compute-1"]


def test_parse_list_without_prefix_returns_everything():
    assert len(multipass.parse_list(SAMPLE)) == 3


def test_parse_list_handles_empty_backend():
    assert multipass.parse_list('{"list": []}', "lab1-") == []


def test_parse_list_tolerates_vm_without_addresses():
    nodes = multipass.parse_list('{"list": [{"name": "lab1-management-1"}]}', "lab1-")
    assert nodes[0].ips == []


def test_lan_ip_skips_the_pod_network():
    node = multipass.Node("lab1-management-1", ["10.244.0.1", "192.168.252.10"])
    assert multipass.lan_ip(node) == "192.168.252.10"


def test_lan_ip_is_empty_when_only_pod_addresses_exist():
    assert multipass.lan_ip(multipass.Node("n", ["10.244.0.1"])) == ""
