"""Where the lab writes things, in particular the CI-vs-clone registry split."""

from pathlib import Path

import pytest

from forgelab import paths


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host with no registry pointer and no override in the environment."""
    monkeypatch.delenv("FORGELAB_REGISTRY_DIR", raising=False)
    monkeypatch.setattr(paths, "REGISTRY_POINTER", tmp_path / "registry_dir")
    return tmp_path / "registry_dir"


def test_falls_back_to_this_checkout(host):
    assert paths.registry_dir() == paths.REPO_ROOT / "cluster_registered"


def test_the_host_pointer_wins_over_the_checkout(host, tmp_path):
    """Bamboo's checkout is throwaway; the pointer names the durable clone."""
    host.write_text(f"{tmp_path}/clone/cluster_registered\n")
    assert paths.registry_dir() == tmp_path / "clone" / "cluster_registered"


def test_an_empty_pointer_is_ignored(host):
    host.write_text("\n")
    assert paths.registry_dir() == paths.REPO_ROOT / "cluster_registered"


def test_the_environment_beats_the_pointer(host, tmp_path, monkeypatch):
    host.write_text(f"{tmp_path}/pointed\n")
    monkeypatch.setenv("FORGELAB_REGISTRY_DIR", str(tmp_path / "override"))
    assert paths.registry_dir() == tmp_path / "override"


def test_repo_root_is_the_checkout_root():
    assert (paths.REPO_ROOT / "Makefile").is_file()
    assert paths.SHARED_DIR == Path(paths.__file__).resolve().parents[2]
