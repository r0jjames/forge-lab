# Auto-publishing Bamboo Specs on every push to main

## Problem

Plans-as-code only holds if the server matches the repo. Today it does not,
by construction: `make specs-publish` is a manual step, so a merged change to
`ProvisionClusterSpec.java` sits in `main` while Bamboo keeps running the
plan definition from whenever someone last remembered to publish. The drift
is silent — nothing compares the two — and it is discovered the worst way,
by a run that behaves like the old spec.

The publish path also spreads its inputs across three places. The list of
spec classes lives in the Makefile's `SPEC_CLASSES`, `lab/README.md` rule 6
tells authors to remember to edit it, and the Bamboo PAT lives in
`bamboo-specs/.credentials`, which is gitignored and therefore absent from
any checkout Bamboo makes. A plan that publishes specs cannot use the file
the Makefile depends on.

## Approach

Two triggers, one core.

The core is a single entrypoint, `publish_specs.py`, that discovers the spec
classes, materializes credentials, and publishes. It runs identically from a
terminal (`make specs-publish`), from a git hook, and from a Bamboo job —
the same CI-agnostic split every other plan in this lab uses.

The two triggers are deliberately redundant:

- A **`pre-push` hook** publishes the moment `main` is pushed from this
  clone. Fast feedback, no polling delay.
- A **`FORGE-SPECS` plan** polls the linked repository and republishes on
  any change to `main`. It catches pushes from other machines, pushes made
  while the lab was off, and merges done in the GitHub UI.

Publishing is an idempotent overwrite, so the two racing is harmless: the
later run rewrites the same definitions with the same content.

GitHub Actions was considered and rejected. Bamboo is reachable only at
`http://localhost:8085` through the `make ui` port-forward on the Rancher
Desktop host; a cloud runner cannot reach it without a tunnel that this lab
has no reason to own.

### The entrypoint

`lab/specspublish/scripts/publish_specs.py`, standard library only, wrapped
in `proc.main` like every other entrypoint.

Steps:

1. Resolve the repo root from `forgelab.paths`, never from the working
   directory — Bamboo runs jobs from its own build directory.
2. Discover spec classes (below).
3. Resolve the PAT (below) and write `bamboo-specs/.credentials` with mode
   `0600` into the checkout it is running in.
4. For each class, run from `bamboo-specs/`:
   `mvn -q compile exec:java -Dexec.mainClass=<class>
   -Dexec.cleanupDaemonThreads=false`, failing the whole run on the first
   non-zero exit.

One flag, `--skip-if-unreachable`, used only by the hook. With it, the
entrypoint TCP-probes the host and port of `SpecConstants.BAMBOO_URL` first;
if the connection is refused it prints one line and exits 0. Without it, an
unreachable server is an error — a Bamboo job that cannot reach its own
server should go red, not pass quietly.

### Discovering spec classes

`SPEC_CLASSES` disappears. The entrypoint globs
`bamboo-specs/src/main/java/lab/*/*Spec.java` and derives the class name
from the path: directory name is the package leaf, file stem is the class,
so `lab/provisioncluster/ProvisionClusterSpec.java` becomes
`lab.provisioncluster.ProvisionClusterSpec`. The list is sorted for a stable
publish order.

This works because the repo already enforces the shape that makes it
unambiguous: one lowercase directory per plan, one `<Name>Spec.java` inside
it, `shared/` holding no spec. The layout rule becomes the mechanism, and
adding a plan stops requiring a Makefile edit that is easy to forget and
invisible when omitted.

The failure mode is a `*Spec.java` without a `main()`, which fails the
publish with Maven's own error. That is acceptable: `lab/README.md` already
requires every spec to have one, and the offline spec test would be the
first thing to catch a malformed spec anyway.

### Credentials

One host file, `~/.forgelab/bamboo_pat`, mode `0600`, containing the token
and nothing else. `FORGELAB_BAMBOO_PAT` in the environment overrides it, so
a one-off run needs no file.

The entrypoint renders `bamboo-specs/.credentials` from it on every run —
the file Maven's Bamboo Specs plugin reads, in the format it already expects
(`token=<pat>`). It stays gitignored, and it is now generated rather than
hand-maintained, which is what lets the same code work in Bamboo's throwaway
checkout.

`~/.forgelab/` is where this lab already keeps host-side secrets and
per-cluster credentials, so the PAT joins material of the same kind under
the same permissions.

A missing PAT is a hard error with a one-line message naming the file. In
hook mode it is still only a warning, because the hook never blocks a push.

### The hook

`infra/githooks/pre-push` — Python 3, executable, no extension, since git
requires that exact filename. It belongs in `infra/` because no plan
executes it.

Git hands `pre-push` one line per ref on stdin:
`<local-ref> <local-sha> <remote-ref> <remote-sha>`. The hook parses those
lines and acts only if some line's remote ref is `refs/heads/main` and its
local sha is not all zeros (a branch deletion). Anything else — a feature
branch, a tag — is a no-op.

When it does act it calls `publish_specs.py --skip-if-unreachable` and then
exits 0 **unconditionally**. Every failure path — Bamboo down, missing PAT,
a spec that will not compile — prints one warning line and lets the push
through. The lab is a personal one and being unable to push while the
cluster is off is a worse failure than a stale plan; the polling plan is the
backstop that eventually reconciles.

`pre-push` fires before the push completes, so a publish followed by a
rejected push leaves the server briefly ahead of `main`. The next push, or
the next poll cycle, corrects it. That window is not worth a post-push
reconciliation mechanism git does not provide.

Installation is `make hooks-install`, which runs
`git config core.hooksPath infra/githooks`. One config line, the hook itself
is versioned, and there are no symlinks to repair after a fresh clone.

### The plan

`lab/specspublish/PublishSpecsSpec.java` — project `FORGE` (alongside PROV
and DEPROV), plan key `SPECS`, name "Publish Specs".

- `linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME))`,
  matching the other plans.
- A `RepositoryPollingTrigger` with a 3-minute period. Polling, not a
  webhook: GitHub cannot reach a Bamboo that only exists behind a local
  port-forward.
- No plan branch management, so only `main` ever builds.
- One stage, `Publish`, one job requiring `agent.role=host`. The host
  requirement is not about tooling this time — it is that `localhost:8085`
  resolves to Bamboo only on the host, through the `make ui` port-forward
  the host agent already depends on, and that `~/.forgelab/bamboo_pat` is a
  host file.
- Tasks: `VcsCheckoutTask` on the default repository, then a `ScriptTask`
  running
  `bamboo-specs/src/main/java/lab/specspublish/scripts/publish_specs.py`.

The plan publishes itself, since it is discovered like any other spec. A
change to `PublishSpecsSpec.java` therefore takes effect on the run *after*
the one that publishes it — normal for self-updating pipelines, and worth a
comment in the spec so nobody debugs it as a bug.

Bootstrapping is one manual `make specs-publish` after this merges. From
then on the loop is closed.

## Changes to existing files

- `Makefile`: `SPEC_CLASSES` deleted; `specs-publish` becomes a call to
  `publish_specs.py`; new `hooks-install` target.
- `lab/README.md`: rule 6 ("register the spec class in the Makefile")
  becomes "nothing to register — the publisher discovers `lab/*/*Spec.java`",
  and the tree gains `specspublish/`.
- `CLAUDE.md`: document `make hooks-install`, the `~/.forgelab/bamboo_pat`
  file, and the contract that a push to `main` republishes every plan.
- `.gitignore`: unchanged; `bamboo-specs/.credentials` is already ignored
  and is now generated.

## Testing

Offline, in `make lint`:

- `src/test/java/lab/specspublish/PublishSpecsSpecTest.java` —
  `EntityPropertiesBuilders.build(...)` on the plan, plus assertions that
  the job carries `agent.role=host` and that the plan has a repository
  polling trigger. Those two are the properties that silently break the
  feature if lost.
- `src/test/python/test_publish_specs.py` — the pure functions:
  - ref-line parsing: `main` update triggers, feature branch does not, tag
    does not, deletion (all-zero local sha) does not, multi-ref push
    containing `main` does.
  - class discovery against a fake tree: two plan directories yield two
    sorted class names; `shared/` and a stray non-`*Spec.java` file are
    ignored.
  - credentials rendering: exact `token=<pat>` content, and that the writer
    requests mode `0600`.
  - PAT resolution precedence: environment beats file, neither present is a
    `LabError`.

`proc.run` is stubbed throughout, so no test shells out to Maven or touches
a real Bamboo.

Manual verification after merge, once:

1. `make specs-publish` — bootstraps the new plan onto the server.
2. `make hooks-install`, then push a trivial spec comment to `main` and
   confirm the hook line appears and the plan reflects the change.
3. Stop the `make ui` port-forward and push again — confirm the skip line
   and that the push still succeeds.
4. Push from the GitHub UI (or with the hook bypassed via `--no-verify`) and
   confirm FORGE-SPECS picks it up within one poll interval.

## Out of scope

- Bamboo's native Repository-Stored Specs. It is the same idea implemented
  by the server, and the server pod does have Maven, but it makes plans
  read-only in the UI and expects specs authored without a `main()` that
  calls `BambooServer.publish`. That is a rewrite of every spec in the repo
  and a separate decision.
- Reacting to pushes on branches other than `main`. Plans are published from
  `main` only; branch experiments stay local.
- Detecting and reporting server-versus-repo drift as its own signal. If the
  two triggers work, drift does not accumulate.
