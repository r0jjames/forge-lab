"""Process helpers and the fatal-error convention every entrypoint uses."""

from __future__ import annotations

import shutil
import subprocess
import sys

DEVNULL = subprocess.DEVNULL


class LabError(Exception):
    """An expected, fatal condition. Reported as `ERROR: <msg>`, exit 1."""


def die(msg: str):
    raise LabError(msg)


def require_tools(*tools: str):
    """Fail unless every named executable is on PATH."""
    for tool in tools:
        if shutil.which(tool) is None:
            die(f"missing tool: {tool}")


def run(*args, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command with its output streaming to this process's stdout/stderr."""
    return subprocess.run([str(a) for a in args], check=check, **kwargs)


def run_out(*args, check: bool = True, **kwargs) -> str:
    """Run a command and return its stdout as text."""
    completed = subprocess.run(
        [str(a) for a in args], check=check, capture_output=True, text=True, **kwargs
    )
    return completed.stdout


def main(entry):
    """Wrap an entrypoint's `main(argv)`.

    Turns the two failure modes we expect — a LabError raised by our own checks,
    and a non-zero exit from a tool we shelled out to — into a one-line message
    on stderr instead of a traceback, mirroring bash `die` + `set -e`.
    """
    try:
        entry(sys.argv[1:])
    except LabError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as err:
        cmd = " ".join(str(a) for a in err.cmd) if isinstance(err.cmd, list) else err.cmd
        print(f"ERROR: command failed ({err.returncode}): {cmd}", file=sys.stderr)
        sys.exit(err.returncode or 1)
    except KeyboardInterrupt:
        sys.exit(130)
