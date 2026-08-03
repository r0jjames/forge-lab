import subprocess

import pytest

from forgelab import proc


def test_require_tools_passes_for_something_always_present():
    proc.require_tools("sh")


def test_require_tools_names_the_missing_tool(monkeypatch):
    monkeypatch.setattr(proc.shutil, "which", lambda _: None)
    with pytest.raises(proc.LabError, match="missing tool: terraform"):
        proc.require_tools("terraform")


def test_run_out_captures_stdout():
    assert proc.run_out("printf", "hello") == "hello"


def test_run_raises_on_failure():
    with pytest.raises(subprocess.CalledProcessError):
        proc.run("false")


def test_run_can_tolerate_failure():
    assert proc.run("false", check=False).returncode != 0


def test_main_reports_lab_errors_without_a_traceback(capsys):
    def entry(_argv):
        proc.die("boom")

    with pytest.raises(SystemExit) as exit_info:
        proc.main(entry)
    assert exit_info.value.code == 1
    assert capsys.readouterr().err.strip() == "ERROR: boom"


def test_main_reports_a_failed_command_with_its_exit_code(capsys):
    def entry(_argv):
        proc.run("false")

    with pytest.raises(SystemExit) as exit_info:
        proc.main(entry)
    assert exit_info.value.code == 1
    assert "command failed (1): false" in capsys.readouterr().err
