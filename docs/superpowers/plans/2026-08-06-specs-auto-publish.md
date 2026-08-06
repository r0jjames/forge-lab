# Bamboo Specs Auto-Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every push to `main` republishes every Bamboo plan in this repo, through a git `pre-push` hook and a polling Bamboo plan that both call one shared entrypoint.

**Architecture:** One CI-agnostic entrypoint, `lab/specspublish/scripts/publish_specs.py`, discovers `lab/*/*Spec.java`, renders `bamboo-specs/.credentials` from `~/.forgelab/bamboo_pat`, and runs `mvn exec:java` per class. A `pre-push` hook calls it in a never-block mode; a new `FORGE-SPECS` Bamboo plan polls `main` and calls it on the host agent. `SPEC_CLASSES` in the Makefile disappears — the filesystem layout becomes the source of truth.

**Tech Stack:** Python 3 (standard library only), Bamboo Specs Java API 12.1.8 + JUnit 4, Maven, GNU Make, git hooks.

Spec: `docs/superpowers/specs/2026-08-06-specs-auto-publish-design.md`.

## Global Constraints

- Python is **standard library only**. The host Bamboo agent has no venv.
- Entrypoint filenames use underscores so the tests can import them; wrap `main(argv)` in `proc.main`; parsing/rendering stays pure and everything that shells out goes through `proc.run`.
- Never resolve paths from the current working directory. Bamboo runs jobs from its own build directory. Use `forgelab.paths`, which derives everything from the package's own location.
- `infra/` may import `forgelab` and nothing else under `lab/`. No plan reaches into another plan's directory. Running an entrypoint as a subprocess is not importing it.
- Commits use Roj's git identity only — no Claude co-author, trailer, or footer.
- Never commit secrets. `bamboo-specs/.credentials` stays gitignored; `~/.forgelab/bamboo_pat` is mode `0600` and lives outside the repo.
- Python tests live in `bamboo-specs/src/test/python/`, Java spec tests in `bamboo-specs/src/test/java/lab/<planid>/`. Both run under `make lint`.
- Bamboo server URL is `http://localhost:8085` (`SpecConstants.BAMBOO_URL`), reachable only through the `make ui` port-forward.

---

### Task 1: Path constants for the publisher

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/shared/python/forgelab/paths.py`
- Test: `bamboo-specs/src/test/python/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.SPECS_ROOT` (`Path`, the Maven project root `<repo>/bamboo-specs`), `paths.LAB_DIR` (`Path`, `<repo>/bamboo-specs/src/main/java/lab`), `paths.BAMBOO_PAT` (`Path`, `~/.forgelab/bamboo_pat`).

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_paths.py`:

```python
def test_lab_dir_is_the_plan_root():
    assert paths.LAB_DIR.name == "lab"
    assert (paths.LAB_DIR / "provisioncluster").is_dir()


def test_specs_root_is_the_maven_project():
    assert (paths.SPECS_ROOT / "pom.xml").is_file()


def test_bamboo_pat_sits_with_the_other_host_secrets():
    assert paths.BAMBOO_PAT == paths.FORGELAB_HOME / "bamboo_pat"
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `pytest bamboo-specs/src/test/python/test_paths.py -v`
Expected: FAIL — `AttributeError: module 'forgelab.paths' has no attribute 'LAB_DIR'`.

- [ ] **Step 3: Add the constants**

In `paths.py`, directly below the existing `REPO_ROOT` assignment:

```python
# <repo>/bamboo-specs — Maven's project root. `mvn exec:java` publishes from here.
SPECS_ROOT = REPO_ROOT / "bamboo-specs"
# .../java/lab — one directory per plan. publish_specs.py discovers specs under it.
LAB_DIR = SHARED_DIR.parent
```

and below the existing `SSH_KEY` line:

```python
# Bamboo personal access token, used only to publish plans. Same home, same
# 0600 posture as the cluster credentials files.
BAMBOO_PAT = FORGELAB_HOME / "bamboo_pat"
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `pytest bamboo-specs/src/test/python/test_paths.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add bamboo-specs/src/main/java/lab/shared/python/forgelab/paths.py \
        bamboo-specs/src/test/python/test_paths.py
git commit -m "feat: add specs-root, lab-dir and bamboo PAT paths"
```

---

### Task 2: Spec discovery, PAT resolution, credentials rendering

The pure core of the publisher: no subprocesses, no network. `main()` comes in Task 3.

**Files:**
- Create: `bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py`
- Modify: `bamboo-specs/src/test/python/conftest.py`
- Test: `bamboo-specs/src/test/python/test_publish_specs.py`

**Interfaces:**
- Consumes: `paths.SPECS_ROOT`, `paths.LAB_DIR`, `paths.BAMBOO_PAT` (Task 1); `proc.die`, `proc.LabError`.
- Produces:
  - `BAMBOO_URL: str` — module constant, mirrors `SpecConstants.BAMBOO_URL`.
  - `spec_classes(lab_dir: Path) -> list[str]` — sorted fully-qualified class names.
  - `resolve_token(env: dict, pat_file: Path) -> str` — raises `LabError` when absent.
  - `render_credentials(token: str) -> str`.
  - `write_credentials(token: str, dest: Path) -> Path` — writes mode `0600`.

- [ ] **Step 1: Put the new scripts directory on the test path**

In `bamboo-specs/src/test/python/conftest.py`, add one entry to the `for path in (...)` tuple, after the `deprovisioncluster` line:

```python
    LAB / "specspublish" / "scripts",
```

- [ ] **Step 2: Write the failing tests**

Create `bamboo-specs/src/test/python/test_publish_specs.py`:

```python
"""The publisher's pure parts: discovery, token resolution, credentials."""

import pytest

import publish_specs
from forgelab.proc import LabError


def make_lab(tmp_path):
    """A stand-in lab/ tree: two plans, a shared dir, and a stray file."""
    (tmp_path / "provisioncluster").mkdir()
    (tmp_path / "provisioncluster" / "ProvisionClusterSpec.java").write_text("")
    (tmp_path / "agentimage").mkdir()
    (tmp_path / "agentimage" / "BuildAgentImageSpec.java").write_text("")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "SpecConstants.java").write_text("")
    (tmp_path / "provisioncluster" / "README.md").write_text("")
    return tmp_path


def test_discovers_one_class_per_plan_directory(tmp_path):
    assert publish_specs.spec_classes(make_lab(tmp_path)) == [
        "lab.agentimage.BuildAgentImageSpec",
        "lab.provisioncluster.ProvisionClusterSpec",
    ]


def test_discovery_ignores_shared_and_non_spec_files(tmp_path):
    classes = publish_specs.spec_classes(make_lab(tmp_path))
    assert not any("shared" in c or "README" in c for c in classes)


def test_discovers_the_real_tree():
    from forgelab import paths

    classes = publish_specs.spec_classes(paths.LAB_DIR)
    assert "lab.provisioncluster.ProvisionClusterSpec" in classes
    assert "lab.deprovisioncluster.DeprovisionClusterSpec" in classes


def test_environment_token_beats_the_file(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("from-file\n")
    env = {"FORGELAB_BAMBOO_PAT": "from-env"}
    assert publish_specs.resolve_token(env, pat) == "from-env"


def test_token_falls_back_to_the_file(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("  from-file\n")
    assert publish_specs.resolve_token({}, pat) == "from-file"


def test_missing_token_names_the_file(tmp_path):
    with pytest.raises(LabError, match="bamboo_pat"):
        publish_specs.resolve_token({}, tmp_path / "bamboo_pat")


def test_empty_token_file_is_an_error(tmp_path):
    pat = tmp_path / "bamboo_pat"
    pat.write_text("\n")
    with pytest.raises(LabError, match="bamboo_pat"):
        publish_specs.resolve_token({}, pat)


def test_credentials_are_the_format_maven_reads():
    assert publish_specs.render_credentials("abc123") == "token=abc123\n"


def test_credentials_are_written_owner_only(tmp_path):
    dest = tmp_path / ".credentials"
    publish_specs.write_credentials("abc123", dest)
    assert dest.read_text() == "token=abc123\n"
    assert oct(dest.stat().st_mode)[-3:] == "600"


def test_credentials_overwrite_an_existing_file(tmp_path):
    dest = tmp_path / ".credentials"
    dest.write_text("token=stale\n")
    publish_specs.write_credentials("fresh", dest)
    assert dest.read_text() == "token=fresh\n"
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `pytest bamboo-specs/src/test/python/test_publish_specs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'publish_specs'`.

- [ ] **Step 4: Write the module (pure parts only)**

Create `bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py`:

```python
#!/usr/bin/env python3
"""Publish every Bamboo Specs plan in this repo to the Bamboo server.

The single publish path. `make specs-publish`, the pre-push hook and the
FORGE-SPECS plan all run this file, so the three cannot drift.

There is no list of plans anywhere: the layout is the list. One directory per
plan under lab/, one <Name>Spec.java inside it, so the classes are discoverable
from the filesystem and adding a plan needs no bookkeeping elsewhere.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import paths, proc  # noqa: E402

# Mirrors SpecConstants.BAMBOO_URL — the Java specs publish to it and this
# script probes it. PublishSpecsSpecTest pins the two together.
BAMBOO_URL = "http://localhost:8085"

USAGE = "usage: publish_specs.py [--skip-if-unreachable]"

# The token env var doubles as the one-off override, so a run needs no file.
TOKEN_ENV = "FORGELAB_BAMBOO_PAT"


def spec_classes(lab_dir: Path) -> list:
    """Every plan spec under lab/, as fully qualified class names.

    lab/provisioncluster/ProvisionClusterSpec.java is
    lab.provisioncluster.ProvisionClusterSpec: the directory is the package
    leaf, the file stem is the class. Sorted, so the publish order is stable.
    """
    return sorted(
        f"lab.{spec.parent.name}.{spec.stem}" for spec in lab_dir.glob("*/*Spec.java")
    )


def resolve_token(env: dict, pat_file: Path) -> str:
    """The Bamboo PAT: environment first, then the host file."""
    from_env = env.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    if pat_file.is_file():
        from_file = pat_file.read_text().strip()
        if from_file:
            return from_file
    proc.die(
        f"no Bamboo token: set {TOKEN_ENV} or put the PAT in {pat_file} "
        "(chmod 600). Bamboo: Profile > Personal access tokens."
    )


def render_credentials(token: str) -> str:
    """The .credentials file the Bamboo Specs Maven plugin reads. Pure."""
    return f"token={token}\n"


def write_credentials(token: str, dest: Path) -> Path:
    """Write .credentials owner-readable only. Returns its path.

    Opened with the mode already restricted rather than write_text() + chmod():
    the latter leaves a world-readable window on a file holding a live token.
    """
    handle = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as out:
        out.write(render_credentials(token))
    return dest
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `pytest bamboo-specs/src/test/python/test_publish_specs.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 6: Commit**

```bash
git add bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py \
        bamboo-specs/src/test/python/test_publish_specs.py \
        bamboo-specs/src/test/python/conftest.py
git commit -m "feat: discover spec classes and materialize bamboo credentials"
```

---

### Task 3: The publish run — reachability probe and the Maven loop

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py`
- Test: `bamboo-specs/src/test/python/test_publish_specs.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces:
  - `server_reachable(url: str, timeout: float = 2.0) -> bool`.
  - `publish(class_name: str)` — one `mvn` invocation, cwd `paths.SPECS_ROOT`.
  - `main(argv: list)` — the entrypoint; accepts only the optional `--skip-if-unreachable`.

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_publish_specs.py`:

```python
@pytest.fixture
def publishes(monkeypatch, tmp_path):
    """Record the commands main() would run; keep it off the network."""
    calls = []
    monkeypatch.setattr(publish_specs.proc, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(publish_specs.proc, "require_tools", lambda *tools: None)
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(publish_specs.paths, "SPECS_ROOT", tmp_path)
    monkeypatch.setenv("FORGELAB_BAMBOO_PAT", "test-token")
    return calls


def test_main_publishes_every_discovered_class(publishes):
    publish_specs.main([])
    published = [
        arg for args, _ in publishes for arg in args if arg.startswith("-Dexec.mainClass=")
    ]
    assert "-Dexec.mainClass=lab.provisioncluster.ProvisionClusterSpec" in published
    assert len(published) == len(publish_specs.spec_classes(publish_specs.paths.LAB_DIR))


def test_main_writes_credentials_next_to_the_pom(publishes, tmp_path):
    publish_specs.main([])
    assert (tmp_path / ".credentials").read_text() == "token=test-token\n"


def test_main_runs_maven_from_the_specs_root(publishes, tmp_path):
    publish_specs.main([])
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in publishes)


def test_skip_flag_returns_quietly_when_bamboo_is_down(monkeypatch, capsys):
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(
        publish_specs.proc, "run", lambda *a, **kw: pytest.fail("must not publish")
    )
    publish_specs.main(["--skip-if-unreachable"])
    assert "skipping" in capsys.readouterr().out


def test_without_the_flag_an_unreachable_server_is_an_error(monkeypatch):
    monkeypatch.setattr(publish_specs, "server_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(
        publish_specs.proc, "run", lambda *a, **kw: pytest.fail("must not publish")
    )
    with pytest.raises(LabError, match="unreachable"):
        publish_specs.main([])


def test_unknown_argument_is_rejected():
    with pytest.raises(LabError, match="usage:"):
        publish_specs.main(["--force"])


def test_reachability_probe_is_false_for_a_closed_port():
    assert publish_specs.server_reachable("http://127.0.0.1:1", timeout=0.5) is False
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `pytest bamboo-specs/src/test/python/test_publish_specs.py -v`
Expected: FAIL — `AttributeError: module 'publish_specs' has no attribute 'server_reachable'`.

- [ ] **Step 3: Add the run logic**

Add to the imports at the top of `publish_specs.py`:

```python
import socket
from urllib.parse import urlparse
```

and append to the module:

```python
def server_reachable(url: str, timeout: float = 2.0) -> bool:
    """True when something accepts a TCP connection at `url`'s host and port.

    A connect check, not an HTTP one: Bamboo answers slowly while it boots and
    all this needs to know is whether the port-forward exists at all.
    """
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def publish(class_name: str):
    """Run one spec's main(), which POSTs the plan to Bamboo."""
    print(f"==> publishing {class_name}")
    proc.run(
        "mvn",
        "-q",
        "compile",
        "exec:java",
        f"-Dexec.mainClass={class_name}",
        "-Dexec.cleanupDaemonThreads=false",
        cwd=paths.SPECS_ROOT,
    )


def main(argv):
    skip_if_down = False
    for arg in argv:
        if arg == "--skip-if-unreachable":
            skip_if_down = True
        else:
            proc.die(f"unknown argument {arg}\n{USAGE}")

    if not server_reachable(BAMBOO_URL):
        # The hook must never block a push; the plan must go red. Same check,
        # opposite verdicts, which is the only thing the flag decides.
        if skip_if_down:
            print(f"==> bamboo unreachable at {BAMBOO_URL} — skipping specs publish")
            return
        proc.die(f"bamboo unreachable at {BAMBOO_URL} (is `make ui` running?)")

    proc.require_tools("mvn")
    classes = spec_classes(paths.LAB_DIR)
    if not classes:
        proc.die(f"no *Spec.java found under {paths.LAB_DIR}")

    creds = write_credentials(
        resolve_token(os.environ, paths.BAMBOO_PAT), paths.SPECS_ROOT / ".credentials"
    )
    print(f"==> credentials: {creds}")
    for class_name in classes:
        publish(class_name)
    print(f"==> published {len(classes)} plan(s) to {BAMBOO_URL}")


if __name__ == "__main__":
    proc.main(main)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `pytest bamboo-specs/src/test/python/test_publish_specs.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Make the entrypoint executable**

```bash
chmod +x bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py
```

- [ ] **Step 6: Publish for real, once, by hand**

Precondition: `make ui` running in another terminal, and the PAT on disk:

```bash
printf '%s' '<your bamboo PAT>' > ~/.forgelab/bamboo_pat && chmod 600 ~/.forgelab/bamboo_pat
bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py
```

Expected: one `==> publishing lab.…` line per existing spec and a final `published 3 plan(s)`. If Bamboo is not up, expect exactly `ERROR: bamboo unreachable at http://localhost:8085 (is `make ui` running?)` and treat that as a pass for this step.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py \
        bamboo-specs/src/test/python/test_publish_specs.py
git commit -m "feat: publish every discovered spec, with a skip mode for hooks"
```

---

### Task 4: Makefile delegates to the entrypoint

**Files:**
- Modify: `Makefile` (the `SPEC_CLASSES` variable near the top, and the `specs-publish` target)

**Interfaces:**
- Consumes: `publish_specs.py` (Task 3).
- Produces: `make specs-publish` with no class list of its own.

- [ ] **Step 1: Delete the class list**

Remove these four lines from the top of the `Makefile`:

```make
SPEC_CLASSES := lab.provisioncluster.ProvisionClusterSpec \
                lab.deprovisioncluster.DeprovisionClusterSpec \
                lab.agentimage.BuildAgentImageSpec
```

- [ ] **Step 2: Replace the target body**

Replace the whole `specs-publish` target with:

```make
.PHONY: specs-publish
specs-publish: ## Publish all Bamboo Specs plans to the server
	$(LAB)/specspublish/scripts/publish_specs.py
```

- [ ] **Step 3: Confirm nothing else referenced the variable**

Run: `grep -n 'SPEC_CLASSES\|\.credentials' Makefile`
Expected: no output. (The credentials check moved into the entrypoint, which now writes the file instead of demanding it.)

- [ ] **Step 4: Run the target**

Run: `make specs-publish`
Expected: same output as Task 3 Step 6 — the publish lines, or the one-line unreachable error if Bamboo is down.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "refactor: make specs-publish call the publisher entrypoint"
```

---

### Task 5: The pre-push hook

Two files: git requires the hook be named exactly `pre-push`, and the repo requires importable underscore filenames for anything under test. The hook is a two-line shim; the logic sits beside it in `pre_push.py`.

**Files:**
- Create: `infra/githooks/pre-push`
- Create: `infra/githooks/pre_push.py`
- Modify: `bamboo-specs/src/test/python/conftest.py`
- Modify: `Makefile` (new `hooks-install` target)
- Test: `bamboo-specs/src/test/python/test_pre_push.py`

**Interfaces:**
- Consumes: `publish_specs.py` (Task 3), run as a subprocess — `infra/` never imports from a plan directory.
- Produces: `touches_main(stdin_text: str) -> bool`, `main(argv: list, stdin_text: str) -> int` (always returns 0).

- [ ] **Step 1: Put the hooks directory on the test path**

In `bamboo-specs/src/test/python/conftest.py`, add to the `for path in (...)` tuple, after the `infra/agent` line:

```python
    REPO_ROOT / "infra" / "githooks",
```

- [ ] **Step 2: Write the failing tests**

Create `bamboo-specs/src/test/python/test_pre_push.py`:

```python
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
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `pytest bamboo-specs/src/test/python/test_pre_push.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pre_push'`.

- [ ] **Step 4: Write the hook logic**

Create `infra/githooks/pre_push.py`:

```python
#!/usr/bin/env python3
"""Republish the Bamboo plans when main is pushed from this clone.

Installed by `make hooks-install` (git config core.hooksPath infra/githooks).
Runs the plan's publisher as a subprocess rather than importing it: infra/
imports forgelab and nothing else under lab/.

This hook never blocks a push. Being unable to push while the lab is switched
off is a worse failure than a stale plan, and the FORGE-SPECS plan reconciles
from the real main on its next poll either way.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH = (
    REPO_ROOT
    / "bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py"
)
MAIN_REF = "refs/heads/main"


def touches_main(stdin_text: str) -> bool:
    """True when this push updates refs/heads/main.

    git feeds pre-push one line per ref on stdin:
        <local ref> <local sha> <remote ref> <remote sha>
    A deletion sends an all-zero local sha — nothing to publish from that.
    """
    for line in stdin_text.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        _, local_sha, remote_ref, _ = fields
        if remote_ref == MAIN_REF and set(local_sha) != {"0"}:
            return True
    return False


def main(argv, stdin_text) -> int:
    if not touches_main(stdin_text):
        return 0
    try:
        subprocess.run(
            [sys.executable, str(PUBLISH), "--skip-if-unreachable"], check=True
        )
    except Exception as err:  # noqa: BLE001 — a push must survive anything here
        print(f"warning: specs publish failed, pushing anyway: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.read()))
```

- [ ] **Step 5: Write the shim git actually invokes**

Create `infra/githooks/pre-push`:

```sh
#!/bin/sh
# git requires this exact filename. The logic lives in pre_push.py because the
# test suite imports it, and dashes are not importable.
exec python3 "$(dirname "$0")/pre_push.py" "$@"
```

Then:

```bash
chmod +x infra/githooks/pre-push infra/githooks/pre_push.py
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `pytest bamboo-specs/src/test/python/test_pre_push.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 7: Add the install target**

Append to the `Makefile`, directly after the `specs-publish` target:

```make
.PHONY: hooks-install
hooks-install: ## Point git at infra/githooks (pushes to main republish the plans)
	git config core.hooksPath infra/githooks
	@echo "core.hooksPath=infra/githooks — a push to main now publishes the specs"
```

- [ ] **Step 8: Install and exercise the hook**

```bash
make hooks-install
printf '%s\n' "refs/heads/main $(git rev-parse HEAD) refs/heads/main $(git rev-parse HEAD)" \
  | infra/githooks/pre-push origin git@github.com:r0jjames/forge-lab.git
```

Expected: with Bamboo up, the publish lines; with it down, exactly one `==> bamboo unreachable … skipping specs publish`. Either way the command exits 0 — check with `echo $?`.

- [ ] **Step 9: Commit**

```bash
git add infra/githooks/pre-push infra/githooks/pre_push.py Makefile \
        bamboo-specs/src/test/python/test_pre_push.py \
        bamboo-specs/src/test/python/conftest.py
git commit -m "feat: publish specs from a pre-push hook on main"
```

---

### Task 6: The FORGE-SPECS plan

**Files:**
- Create: `bamboo-specs/src/main/java/lab/specspublish/PublishSpecsSpec.java`
- Test: `bamboo-specs/src/test/java/lab/specspublish/PublishSpecsSpecTest.java`

**Interfaces:**
- Consumes: `SpecConstants.BAMBOO_URL`, `SpecConstants.REPO_NAME`; `publish_specs.py` (Task 3) as the script task body; `publish_specs.BAMBOO_URL` (pinned by the test).
- Produces: plan `FORGE-SPECS`, discovered and published by `publish_specs.py` like every other spec.

- [ ] **Step 1: Write the failing test**

Create `bamboo-specs/src/test/java/lab/specspublish/PublishSpecsSpecTest.java`:

```java
package lab.specspublish;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import lab.shared.SpecConstants;
import org.junit.Test;

public class PublishSpecsSpecTest {

    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
    }

    @Test
    public void publishRunsOnTheHostAgent() {
        // Only the host reaches localhost:8085 through the `make ui`
        // port-forward, and only the host has ~/.forgelab/bamboo_pat.
        PlanProperties plan = EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
        assertEquals("Publish", plan.getStages().get(0).getName());
        assertTrue(
                "the publish job must require agent.role=host",
                plan.getStages().get(0).getJobs().get(0).getRequirements().stream()
                        .anyMatch(r -> "agent.role".equals(r.getKey())
                                && "host".equals(r.getMatchValue())));
    }

    @Test
    public void planPollsTheRepository() {
        // Without a trigger the plan publishes nothing and the drift this
        // feature exists to kill comes straight back.
        PlanProperties plan = EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
        assertTrue(
                "the plan must carry a repository polling trigger",
                plan.getTriggers().stream()
                        .anyMatch(t -> t.getClass().getSimpleName().startsWith("RepositoryPolling")));
    }

    @Test
    public void bambooUrlMatchesThePublisher() {
        // publish_specs.py probes this URL before publishing to it. Drift here
        // makes the hook skip silently against a server that is actually up.
        Path publisher = Path.of(
                "src/main/java/lab/specspublish/scripts/publish_specs.py");
        List<String> lines = Files.readAllLines(publisher);
        assertTrue(
                "publish_specs.BAMBOO_URL must equal SpecConstants.BAMBOO_URL",
                lines.contains("BAMBOO_URL = \"" + SpecConstants.BAMBOO_URL + "\""));
    }
}
```

Note: `bambooUrlMatchesThePublisher` reads a repo-relative path because Maven runs tests with `bamboo-specs/` as the working directory — the same trick `ProvisionClusterSpecTest.placeholdersMatchPlanvars` uses.

- [ ] **Step 2: Run the test and watch it fail**

Run: `mvn -f bamboo-specs/pom.xml -q test`
Expected: FAIL — compilation error, `PublishSpecsSpec` does not exist.

- [ ] **Step 3: Write the spec**

Create `bamboo-specs/src/main/java/lab/specspublish/PublishSpecsSpec.java`:

```java
package lab.specspublish;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.configuration.ConcurrentBuilds;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.builders.trigger.RepositoryPollingTrigger;
import com.atlassian.bamboo.specs.util.BambooServer;
import java.time.Duration;
import lab.shared.SpecConstants;

/**
 * Republishes every plan in this repo whenever main changes.
 *
 * <p>The pre-push hook covers pushes from the lab's own clone; this plan covers
 * everything else — another machine, a merge in the GitHub UI, a push made while
 * the lab was switched off. Polling rather than a webhook because GitHub cannot
 * reach a Bamboo that only exists behind a local port-forward.
 *
 * <p>This spec is discovered by publish_specs.py like any other, so the plan
 * republishes itself. A change here therefore takes effect on the run after the
 * one that publishes it. That is not a bug.
 */
@BambooSpec
public class PublishSpecsSpec {

    /** Long enough to stay quiet, short enough that a merge lands while you watch. */
    static final Duration POLL_PERIOD = Duration.ofMinutes(3);

    Plan plan() {
        return new Plan(
                new Project().key(new BambooKey("FORGE")).name("forge-lab"),
                "Publish Specs", new BambooKey("SPECS"))
                .description("Republish every Bamboo Specs plan from main")
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))
                .pluginConfigurations(new ConcurrentBuilds().useSystemWideDefault(false))
                .triggers(new RepositoryPollingTrigger().pollingPeriod(POLL_PERIOD))
                .stages(
                        new Stage("Publish").jobs(
                                new Job("Publish", new BambooKey("PUB"))
                                        // Host-only: localhost:8085 is Bamboo only through the
                                        // `make ui` port-forward, and the PAT is a host file.
                                        .requirements(new Requirement("agent.role")
                                                .matchValue("host")
                                                .matchType(Requirement.MatchType.EQUALS))
                                        .tasks(
                                                new VcsCheckoutTask().description("checkout")
                                                        .checkoutItems(new CheckoutItem().defaultRepository()),
                                                new ScriptTask().description("publish specs")
                                                        .inlineBody("bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py"))));
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(SpecConstants.BAMBOO_URL);
        server.publish(new PublishSpecsSpec().plan());
    }
}
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `mvn -f bamboo-specs/pom.xml -q test`
Expected: PASS, all spec tests including the four new ones.

If `pollingPeriod(Duration)` does not compile, check the installed API with
`unzip -l ~/.m2/repository/com/atlassian/bamboo/bamboo-specs-api/12.1.8/*.jar | grep -i trigger`
and use the builder method that class exposes — the requirement is a polling trigger, not a particular method name.

- [ ] **Step 5: Confirm discovery picks the new spec up**

Run: `pytest bamboo-specs/src/test/python/test_publish_specs.py -k real_tree -v`
Expected: PASS — and the class list now includes `lab.specspublish.PublishSpecsSpec`, since discovery is a glob.

- [ ] **Step 6: Publish and eyeball the plan**

With `make ui` running:

```bash
make specs-publish
```

Expected: a `==> publishing lab.specspublish.PublishSpecsSpec` line. Then open http://localhost:8085, find **forge-lab > Publish Specs**, and confirm the trigger reads as repository polling every 3 minutes.

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/specspublish/PublishSpecsSpec.java \
        bamboo-specs/src/test/java/lab/specspublish/PublishSpecsSpecTest.java
git commit -m "feat: add FORGE-SPECS plan republishing every spec from main"
```

---

### Task 7: Documentation and full-suite verification

**Files:**
- Modify: `bamboo-specs/src/main/java/lab/README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code depends on.

- [ ] **Step 1: Update the plan-authoring contract**

In `bamboo-specs/src/main/java/lab/README.md`:

Add to the tree diagram, after the `agentimage/` block:

```
└── specspublish/               # FORGE-SPECS
    ├── PublishSpecsSpec.java
    └── scripts/publish_specs.py
```

Replace rule 6 under "Adding a plan" with:

```markdown
6. Nothing to register. `specspublish/scripts/publish_specs.py` discovers
   `lab/*/*Spec.java` and derives the class from the path, so the directory
   layout is the plan list. Your spec needs a `main()` that calls
   `BambooServer.publish` — that is what the publisher invokes.
```

- [ ] **Step 2: Update the project instructions**

In `CLAUDE.md`, under "Commands", replace nothing and add after the `make lint` bullet:

```markdown
- `make hooks-install` — one-time: `git config core.hooksPath infra/githooks`,
  so a push to `main` republishes every plan. The hook skips quietly when
  Bamboo is unreachable and never blocks a push
- `make specs-publish` — publish every plan now. Needs `make ui` running and
  a Bamboo PAT in `~/.forgelab/bamboo_pat` (chmod 600); `FORGELAB_BAMBOO_PAT`
  overrides it for one run. `bamboo-specs/.credentials` is generated from it,
  never hand-maintained
```

and add to "Conventions":

```markdown
- Plans are published two ways and both call
  `lab/specspublish/scripts/publish_specs.py`: the `pre-push` hook on `main`,
  and the FORGE-SPECS plan polling `main` every 3 minutes. There is no list of
  spec classes anywhere — the publisher globs `lab/*/*Spec.java`, so adding a
  plan directory is the whole registration step
```

- [ ] **Step 3: Run every check**

Run: `make lint`
Expected: pytest green (including `test_publish_specs.py` and `test_pre_push.py`), terraform fmt/validate green, ansible-lint green, `mvn test` green.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md bamboo-specs/src/main/java/lab/README.md
git commit -m "docs: document specs auto-publish and drop the class registry step"
```

- [ ] **Step 5: End-to-end verification on main**

After merging to `main` (with `make ui` and the host agent running):

1. `make specs-publish` once, to bootstrap FORGE-SPECS onto the server.
2. Push a trivial comment change to a spec file; confirm the hook prints publish lines and the push succeeds.
3. Stop the `make ui` port-forward, push again; confirm the skip line and that the push still succeeds.
4. Restart the port-forward, push with `git push --no-verify`; confirm FORGE-SPECS builds on the host agent within one poll interval and goes green.

Expected: the plan's build log ends with `published N plan(s) to http://localhost:8085`, N matching the number of `lab/*/*Spec.java` files.
