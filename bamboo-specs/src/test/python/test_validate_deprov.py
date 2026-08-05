"""The DEPROV plan's Validate stage: cluster_name and nothing else."""

import pytest

import validate_deprov
from forgelab.proc import LabError


def test_accepts_a_well_formed_name(capsys):
    validate_deprov.main(["lab1"])
    assert "==> cluster_name lab1" in capsys.readouterr().out


def test_rejects_a_malformed_name():
    with pytest.raises(LabError, match="cluster_name must match"):
        validate_deprov.main(["Lab1"])


def test_rejects_a_missing_name():
    with pytest.raises(LabError, match="usage:"):
        validate_deprov.main([])
