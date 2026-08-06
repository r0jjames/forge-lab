"""The pre-push hook: publishes on main, and never blocks a push."""

import subprocess

import pre_push

MAIN = "refs/heads/main abc123 refs/heads/main def456"
BRANCH = "refs/heads/feat abc123 refs/heads/feat def456"
TAG = "refs/tags/v1 abc123 refs/tags/v1 def456"
DELETE = "(delete) " + "0" * 40 + " refs/heads/main def456"


def test_a_push_to_main_publishes():
    assert pre_push.touches_main(MAIN + "\n") is True


def test_a_feature_branch_does_not():
    assert pre_push.touches_main(BRANCH + "\n") is False


def test_a_tag_does_not():
    assert pre_push.touches_main(TAG + "\n") is False


def test_deleting_main_does_not():
    assert pre_push.touches_main(DELETE + "\n") is False


def test_a_multi_ref_push_containing_main_does():
    assert pre_push.touches_main(f"{BRANCH}\n{MAIN}\n") is True


def test_empty_stdin_does_not():
    assert pre_push.touches_main("") is False


def test_malformed_lines_are_ignored():
    assert pre_push.touches_main("garbage\n\n") is False


def test_main_runs_the_publisher_in_skip_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert pre_push.main([], MAIN + "\n") == 0
    assert calls and calls[0][-1] == "--skip-if-unreachable"
    assert str(pre_push.PUBLISH) in calls[0]


def test_the_publisher_path_is_real():
    # str(PUBLISH) in the previous test only pins the constant against
    # itself; this pins it against the filesystem, so a rename across the
    # infra/ -> lab/ boundary fails loudly instead of publishing nothing.
    assert pre_push.PUBLISH.is_file()


def test_main_skips_the_publisher_off_main(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(AssertionError("ran"))
    )
    assert pre_push.main([], BRANCH + "\n") == 0


def test_a_failing_publish_still_lets_the_push_through(monkeypatch, capsys):
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", boom)
    assert pre_push.main([], MAIN + "\n") == 0
    assert "warning" in capsys.readouterr().err


def test_a_ctrl_c_during_publish_still_lets_the_push_through(monkeypatch, capsys):
    def boom(cmd, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", boom)
    assert pre_push.main([], MAIN + "\n") == 0
    assert "warning" in capsys.readouterr().err


def test_a_hung_publish_still_lets_the_push_through(monkeypatch, capsys):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", boom)
    assert pre_push.main([], MAIN + "\n") == 0
    assert "warning" in capsys.readouterr().err


def test_main_bounds_the_publish_subprocess_with_a_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(kw))
    assert pre_push.main([], MAIN + "\n") == 0
    assert calls[0]["timeout"] == pre_push.PUBLISH_TIMEOUT
