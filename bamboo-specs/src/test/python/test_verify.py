import verify


def test_nodes_ready_accepts_an_all_ready_cluster():
    out = (
        "lab1-mgmt-1     Ready    control-plane   5m    v1.29.0\n"
        "lab1-compute-1  Ready    <none>          4m    v1.29.0\n"
    )
    assert verify.nodes_ready(out)


def test_nodes_ready_rejects_a_node_still_joining():
    out = (
        "lab1-mgmt-1     Ready       control-plane   5m   v1.29.0\n"
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
