"""Orchestration tests: patch proc.run and assert on the argv terraform gets.

These pin the invariants that have actually bitten this lab — -parallelism=1
surviving, the retry-once behaviour, and select-or-create for workspaces.
"""

import subprocess

import pytest

from forgelab import terraform


class FakeRun:
    """Records argv per call and replays a scripted list of return codes."""

    def __init__(self, returncodes=()):
        self.calls = []
        self.returncodes = list(returncodes)

    def __call__(self, *args, check=True, **kwargs):
        argv = [str(a) for a in args]
        self.calls.append(argv)
        code = self.returncodes.pop(0) if self.returncodes else 0
        if code != 0 and check:
            raise subprocess.CalledProcessError(code, argv)
        return subprocess.CompletedProcess(argv, code)


@pytest.fixture
def fake_run(monkeypatch):
    def install(returncodes=()):
        runner = FakeRun(returncodes)
        monkeypatch.setattr(terraform.proc, "run", runner)
        monkeypatch.setattr(terraform.time, "sleep", lambda _: None)
        return runner

    return install


def test_every_call_targets_the_shared_terraform_root(fake_run):
    runner = fake_run()
    terraform.init()
    assert runner.calls[0][0] == "terraform"
    assert runner.calls[0][1] == f"-chdir={terraform.paths.TF_DIR}"


def test_apply_keeps_parallelism_one(fake_run):
    runner = fake_run()
    terraform.apply_retry("-var", "cluster_name=lab1")
    assert "-parallelism=1" in runner.calls[0]
    assert runner.calls[0][-2:] == ["-var", "cluster_name=lab1"]


def test_apply_does_not_retry_when_it_succeeds(fake_run):
    runner = fake_run([0])
    terraform.apply_retry()
    assert len(runner.calls) == 1


def test_apply_retries_once_on_failure(fake_run):
    runner = fake_run([1, 0])
    terraform.apply_retry("-input=false")
    assert len(runner.calls) == 2
    assert runner.calls[0] == runner.calls[1]


def test_apply_raises_when_the_retry_also_fails(fake_run):
    runner = fake_run([1, 1])
    with pytest.raises(subprocess.CalledProcessError):
        terraform.apply_retry()
    assert len(runner.calls) == 2


def test_workspace_select_or_new_reuses_an_existing_workspace(fake_run):
    runner = fake_run([0])
    terraform.workspace_select_or_new("lab1")
    assert len(runner.calls) == 1
    assert runner.calls[0][-3:] == ["workspace", "select", "lab1"]


def test_workspace_select_or_new_creates_a_missing_workspace(fake_run):
    runner = fake_run([1, 0])
    terraform.workspace_select_or_new("lab1")
    assert runner.calls[1][-3:] == ["workspace", "new", "lab1"]


def test_workspace_delete_leaves_default_selected(fake_run):
    runner = fake_run()
    terraform.workspace_delete("lab1")
    assert runner.calls[0][-3:] == ["workspace", "select", "default"]
    assert runner.calls[1][-3:] == ["workspace", "delete", "lab1"]


def test_destroy_never_raises(fake_run):
    fake_run([1])
    terraform.destroy("-input=false")
