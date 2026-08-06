"""Per-cluster configuration files under cluster_configs/.

Replaces the flat `<cluster>.tfvars` files. One YAML file states a cluster's
type, its node sizing, and which technologies it runs — and everything
downstream (the Terraform nodes map, the Ansible inventory groups, the registry
sizing) is derived from it, so those can no longer disagree.

The host agent has no venv and this package is standard library only, so the
parser is hand-written. It accepts a deliberately small subset and rejects the
rest by name rather than tolerating half of YAML: a parser that quietly handles
sequences but not anchors teaches the reader that the whole language works here.
"""

from __future__ import annotations

import re

from . import paths
from .proc import die

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _fail(source, lineno, message):
    die(f"{source}:{lineno}: {message}")


def parse(text: str, source: str) -> dict:
    """Nested plain dicts from the accepted subset. Every scalar is a str.

    Accepted: two-space block mappings, `key: value`, `key:` openers, `#`
    comments (whole-line and trailing), and blank lines. Values may be wrapped
    in matching single or double quotes.
    """
    root: dict = {}
    # The sentinel indent is -2 so a top-level key at indent 0 reads as one
    # level in from it, the same relationship every other pair has.
    stack = [(-2, root)]

    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            _fail(source, lineno, "tabs are not allowed; indent with two spaces")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            _fail(source, lineno, "sequences are not supported")
        if stripped.startswith("---") or stripped.startswith("..."):
            _fail(source, lineno, "document markers are not supported")

        content = raw.split("#", 1)[0].rstrip()
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            _fail(source, lineno, f"indent of {indent} is not a multiple of two")
        if ":" not in content:
            _fail(source, lineno, f"expected 'key: value' or 'key:', got {stripped!r}")

        key, _, value = content.partition(":")
        key, value = key.strip(), value.strip()
        if not _KEY_RE.match(key):
            _fail(source, lineno, f"invalid key {key!r}")
        if value[:1] in ("{", "["):
            _fail(source, lineno, "flow collections are not supported")
        if value[:1] in ("&", "*"):
            _fail(source, lineno, "anchors and aliases are not supported")
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        while indent <= stack[-1][0]:
            stack.pop()
        if indent > stack[-1][0] + 2:
            _fail(source, lineno, f"indent of {indent} jumps more than one level")

        parent = stack[-1][1]
        if key in parent:
            _fail(source, lineno, f"duplicate key {key!r}")
        if value:
            parent[key] = value
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))

    return root
