"""Where the lab writes things, in particular the CI-vs-clone registry split."""

import importlib
from pathlib import Path

import pytest

from forgelab import paths


@pytest.fixture
def reloaded_paths(monkeypatch):
    """Re-import paths so module-level environment reads take effect, then undo."""

    def reload_with(value):
        if value is None:
            monkeypatch.delenv("FORGELAB_REGISTRY_DIR", raising=False)
        else:
            monkeypatch.setenv("FORGELAB_REGISTRY_DIR", value)
        return importlib.reload(paths)

    yield reload_with
    monkeypatch.delenv("FORGELAB_REGISTRY_DIR", raising=False)
    importlib.reload(paths)


def test_registry_lives_in_the_repo_by_default(reloaded_paths):
    module = reloaded_paths(None)
    assert module.REGISTRY_DIR == module.REPO_ROOT / "cluster_registered"


def test_the_environment_moves_the_registry_off_the_checkout(reloaded_paths, tmp_path):
    """Bamboo hands each plan its own throwaway checkout; both must write here."""
    module = reloaded_paths(str(tmp_path / "clone" / "cluster_registered"))
    assert module.REGISTRY_DIR == tmp_path / "clone" / "cluster_registered"


def test_repo_root_is_the_checkout_root(reloaded_paths):
    module = reloaded_paths(None)
    assert (module.REPO_ROOT / "Makefile").is_file()
    assert module.SHARED_DIR == Path(module.__file__).resolve().parents[2]
