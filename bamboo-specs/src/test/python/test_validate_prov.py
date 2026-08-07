"""The PROV plan's Validate stage: what it reports and what it rejects."""

import pytest

import validate_prov as validate
from forgelab import clusterconfig
from forgelab.proc import LabError

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
        cpu: 4
        memory: 8G
        disk: 40G
  opensearch:
    enabled: false
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
"""


def lines(text=CONFIG):
    return validate.report("lab1", clusterconfig.from_text(text, "lab1_cluster.yaml"))


def test_reports_the_name_type_technologies_and_config():
    out = "\n".join(lines())
    assert "==> cluster_name   lab1" in out
    assert "==> cluster_type   k8s" in out
    assert "==> technologies   hdfs" in out
    assert "==> config         lab1_cluster.yaml" in out


def test_reports_no_technologies_as_none():
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    assert "==> technologies   none" in "\n".join(lines(text))


def rollup_row(role, text=CONFIG):
    """A roll-up line as its whitespace-separated columns."""
    for line in lines(text):
        columns = line.split()
        if columns and columns[0] == role:
            return columns
    raise AssertionError(f"no roll-up row for {role}")


def test_rolls_up_every_role_with_its_sizing():
    assert rollup_row("management") == ["management", "1", "2", "4G", "20G"]
    assert rollup_row("hdfs-datanode") == ["hdfs-datanode", "3", "4", "8G", "40G"]


def test_the_roll_up_has_a_header():
    assert ["ROLE", "N", "CPU", "MEM", "DISK"] in [l.split() for l in lines()]


def test_a_disabled_technology_is_absent_from_the_roll_up():
    assert "opensearch" not in "\n".join(lines())


def test_totals_the_vms_cpu_and_memory():
    """1x2 + 2x2 + 1x2 + 3x4 = 20 vCPU; 4 + 2x3 + 4 + 3x8 = 38G; 7 VMs."""
    out = "\n".join(lines())
    assert "7 VMs" in out
    assert "20 vCPU" in out
    assert "38G RAM" in out


def test_main_rejects_an_invalid_config(configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        validate.main(["lab1"])


def test_main_names_a_missing_config(configs_dir):
    with pytest.raises(LabError, match="no config at .*lab1_cluster.yaml"):
        validate.main(["lab1"])


def test_main_prints_the_resolved_run(configs_dir, capsys):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    validate.main(["lab1", ""])
    assert "==> cluster_type   k8s" in capsys.readouterr().out
