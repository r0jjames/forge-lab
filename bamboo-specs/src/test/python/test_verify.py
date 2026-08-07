import verify


def test_nodes_ready_accepts_an_all_ready_cluster():
    out = (
        "lab1-management-1     Ready    control-plane   5m    v1.29.0\n"
        "lab1-compute-1  Ready    <none>          4m    v1.29.0\n"
    )
    assert verify.nodes_ready(out)


def test_nodes_ready_rejects_a_node_still_joining():
    out = (
        "lab1-management-1     Ready       control-plane   5m   v1.29.0\n"
        "lab1-compute-1  NotReady    <none>          1m   v1.29.0\n"
    )
    assert not verify.nodes_ready(out)


def test_nodes_ready_rejects_ready_with_a_taint_suffix():
    assert not verify.nodes_ready("n1  Ready,SchedulingDisabled  <none>  5m  v1.29.0\n")


def test_nodes_ready_rejects_empty_output():
    assert not verify.nodes_ready("")
    assert not verify.nodes_ready("\n  \n")


def test_nodes_ready_ignores_trailing_blank_lines():
    assert verify.nodes_ready("n1  Ready  <none>  5m  v1.29.0\n\n")


def test_default_storage_class_finds_the_annotated_one():
    text = (
        "local-path (default)   rancher.io/local-path   Delete   "
        "WaitForFirstConsumer   false   3m\n"
    )
    assert verify.default_storage_class(text) == "local-path"


def test_default_storage_class_is_empty_when_none_is_default():
    text = "local-path   rancher.io/local-path   Delete   Immediate   false   3m\n"
    assert verify.default_storage_class(text) == ""


def test_default_storage_class_is_empty_when_there_are_no_classes():
    assert verify.default_storage_class("") == ""


def test_default_storage_class_ignores_a_provisioner_named_default():
    text = "fast   example.io/default   Delete   Immediate   false   3m\n"
    assert verify.default_storage_class(text) == ""


def test_field_from_reads_the_access_token():
    assert verify.field_from('{"access_token": "abc.def"}', "access_token") == "abc.def"


def test_field_from_reads_the_issuer():
    payload = '{"issuer": "http://1.2.3.4:30080/realms/forgelab"}'
    assert verify.field_from(payload, "issuer").endswith("/realms/forgelab")


def test_field_from_is_empty_when_the_key_is_missing():
    assert verify.field_from('{"error": "invalid_grant"}', "access_token") == ""


def test_field_from_is_empty_on_malformed_json():
    assert verify.field_from("<html>404</html>", "access_token") == ""


def test_field_from_is_empty_on_a_json_array():
    assert verify.field_from("[1, 2]", "access_token") == ""


def test_field_from_is_empty_on_a_non_string_value():
    assert verify.field_from('{"access_token": 5}', "access_token") == ""


DFSADMIN_REPORT = """Configured Capacity: 126421467136 (117.74 GB)
Present Capacity: 112233445566 (104.5 GB)
DFS Remaining: 112233445566 (104.5 GB)

-------------------------------------------------
Live datanodes (3):

Name: 192.168.252.21:9866 (lab1-datanode-1)
Hostname: lab1-datanode-1
Decommission Status : Normal
"""


def test_live_datanodes_counts_the_reported_nodes():
    assert verify.live_datanodes(DFSADMIN_REPORT) == 3


def test_live_datanodes_is_zero_when_the_section_is_absent():
    assert verify.live_datanodes("Configured Capacity: 0 (0 B)\n") == 0


def test_live_datanodes_reads_a_single_node_cluster():
    assert verify.live_datanodes("Live datanodes (1):\n") == 1


def test_live_datanodes_is_zero_on_an_empty_report():
    assert verify.live_datanodes("") == 0


def test_live_datanodes_ignores_the_dead_datanodes_section():
    text = "Live datanodes (2):\n\nDead datanodes (5):\n"
    assert verify.live_datanodes(text) == 2


HEALTH_PAYLOAD = '{"status": "green", "number_of_nodes": 3}'


def test_cluster_nodes_reads_a_well_formed_health_payload():
    assert verify.cluster_nodes(HEALTH_PAYLOAD) == 3


def test_cluster_nodes_is_zero_when_the_key_is_missing():
    assert verify.cluster_nodes('{"status": "green"}') == 0


def test_cluster_nodes_is_zero_on_malformed_json():
    assert verify.cluster_nodes("<html>404</html>") == 0


def test_cluster_nodes_is_zero_on_a_json_array():
    assert verify.cluster_nodes('[{"number_of_nodes": 3}]') == 0


def test_cluster_nodes_is_zero_on_a_non_integer_value():
    assert verify.cluster_nodes('{"number_of_nodes": "three"}') == 0


def test_cluster_status_reads_a_well_formed_health_payload():
    assert verify.cluster_status(HEALTH_PAYLOAD) == "green"


def test_cluster_status_is_empty_when_the_key_is_missing():
    assert verify.cluster_status('{"number_of_nodes": 3}') == ""


def test_cluster_status_is_empty_on_malformed_json():
    assert verify.cluster_status("<html>404</html>") == ""


def test_cluster_status_is_empty_on_a_json_array():
    assert verify.cluster_status('[{"status": "green"}]') == ""


def test_doc_count_reads_a_well_formed_count_payload():
    assert verify.doc_count('{"count": 1421}') == 1421


def test_doc_count_is_zero_for_a_zero_count():
    assert verify.doc_count('{"count": 0}') == 0


def test_doc_count_is_zero_when_the_key_is_missing():
    assert verify.doc_count('{"_shards": {}}') == 0


def test_doc_count_is_zero_on_malformed_json():
    assert verify.doc_count("<html>404</html>") == 0


def test_doc_count_is_zero_on_a_json_array():
    assert verify.doc_count('[{"count": 5}]') == 0


def test_doc_count_is_zero_on_a_non_integer_value():
    assert verify.doc_count('{"count": "five"}') == 0
