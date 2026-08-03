"""Terraform invocations, always against the shared workspace-per-cluster root."""

from __future__ import annotations

import sys
import time

from . import paths, proc

RETRY_DELAY_SECONDS = 10


def _tf(*args, **kwargs):
    return proc.run("terraform", f"-chdir={paths.TF_DIR}", *args, **kwargs)


def init():
    _tf("init", "-input=false", stdout=proc.DEVNULL)


def workspace_select(name: str) -> bool:
    """True when the workspace exists and is now selected."""
    return _tf(
        "workspace", "select", name, check=False, stderr=proc.DEVNULL
    ).returncode == 0


def workspace_select_or_new(name: str):
    if not workspace_select(name):
        _tf("workspace", "new", name)


def workspace_delete(name: str):
    _tf("workspace", "select", "default")
    _tf("workspace", "delete", name, check=False)


def apply_retry(*args):
    """Apply, retrying once — the multipass provider has transient failures (R4).

    -parallelism=1 is load-bearing, not tuning: concurrent `multipass launch`
    calls race in multipassd's MAC allocation and every VM of the batch comes up
    with the SAME MAC, so DHCP hands them all one lease. The symptom is three
    nodes sharing an IP and ansible dying with "No route to host". The provider
    exposes no mac attribute, so serializing the launches is the only lever.
    """
    apply_args = ("apply", "-auto-approve", "-parallelism=1", *args)
    if _tf(*apply_args, check=False).returncode == 0:
        return
    print(
        f"terraform apply failed once — retrying in {RETRY_DELAY_SECONDS}s "
        "(multipass flakiness)",
        file=sys.stderr,
    )
    time.sleep(RETRY_DELAY_SECONDS)
    _tf(*apply_args)


def destroy(*args):
    """Best-effort destroy — the backend sweep that follows is the real guarantee."""
    _tf("destroy", "-auto-approve", *args, check=False)
