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
