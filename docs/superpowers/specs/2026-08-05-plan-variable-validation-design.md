# Plan variable validation: fail fast, self-documenting defaults

## Problem

The Provision plan takes three variables — `cluster_name`, `cluster_type`,
`addons` — and the run dialog says nothing about what they accept. Two
defaults are empty strings, so a first-time run gives no hint that
`cluster_type` is `k8s` or `dcos`, or that the addon list is exactly
`keycloak`, `hdfs`, `opensearch`.

The validation that does exist runs too late and in the wrong place. In
`provision.py` the `cluster_type` and `addons` checks sit below
`proc.require_tools` and below `multipass.list_vms`, and the whole job
carries `agent.role=host`, so learning that you typed `splunk` costs a wait
for the host agent plus a multipass round trip. `CLUSTER_NAME_RE` is
duplicated verbatim in `provision.py` and `deprovision.py`.

`CLAUDE.md` and the code also disagree about the empty addon list. The doc
says an empty `ADDONS=` "disables all addons"; `tfvars.resolve_addons`
treats an empty override as "fall back to the cluster's tfvars". Only one
can be true, and neither today offers a way to say "install nothing"
without editing a tfvars file.

## Approach

One shared module owns the rules. A dedicated Bamboo stage runs them before
anything expensive, on any agent. The plan variables' default values double
as the placeholders that document them.

### Placeholders as defaults

Bamboo's Specs API models a plan variable as a key and a value —
`new Variable(String, String)`. There is no description or hint field. So a
placeholder can only live in the default value, and the pipeline has to
recognize it and treat it as "not set":

```java
new Variable("cluster_name", "lab1"),
new Variable("cluster_type", "k8s | dcos"),
new Variable("addons",       "hdfs,keycloak,opensearch (or none)")
```

The run dialog now states the legal values, and an unedited run still works
— the placeholder resolves to whatever the cluster's tfvars says, which is
the behaviour an empty default had before.

Both placeholders read as menus, and neither is a legal value. That matters
for `addons`, where a bare `hdfs,keycloak,opensearch` would be ambiguous: a
user who genuinely wants all three would type exactly the sentinel and get
the tfvars list instead. The `(or none)` suffix removes the collision and
documents the `none` keyword in the same breath.

The placeholder is matched **exactly**. `k8s|dcos` without spaces is a
validation failure, not a synonym for "unset". A sentinel that matches
loosely is a sentinel that eventually eats real input.

### Resolution rules

`cluster_type` and `addons` keep their existing precedence — the plan
variable beats the cluster's tfvars — with the placeholder folded in as a
third way of saying "no override":

| input | `cluster_type` | `addons` |
| --- | --- | --- |
| empty | tfvars | tfvars |
| the exact placeholder string | tfvars | tfvars |
| `k8s` / `dcos` | that value | — |
| `none` | — | zero addons |
| `hdfs` | — | `hdfs` only |
| `k9s` / `splunk` | fail | fail |
| `none,hdfs` | — | fail |

`none` is the answer to the `CLAUDE.md` contradiction: blank means tfvars,
and `none` is how a run says "install nothing" without editing a file.
Combining `none` with a real addon name is a failure rather than a silent
winner, because either reading of it is a guess.

Names are trimmed, de-duplicated, and kept in the order given. Errors name
the offending value, where it came from (`the ADDONS override` versus the
tfvars path), and the legal set — the shape `resolve_cluster_type` already
uses.

### Where the code lives

**`lab/shared/python/forgelab/planvars.py`** — new. It holds the placeholder
strings, the `none` keyword, the cluster-name pattern (absorbing both copies
of `CLUSTER_NAME_RE`), and the resolution rules above.

`resolve_cluster_type` and `resolve_addons` **move here** from `tfvars.py`
rather than staying there and calling back: `planvars` needs `CLUSTER_TYPES`
and `ADDONS`, and having `tfvars` reach forward for the placeholder rules
would make the two modules import each other. `tfvars.py` keeps the legal
sets and the file parsing; the dependency points one way, `planvars` →
`tfvars`.

**`lab/provisioncluster/scripts/validate_prov.py`** — new, thin. Parses
argv, calls `planvars`, prints the resolved plan, exits. It resolves against
the tfvars file too, so it is the one place that reports what a run will
actually do:

```
==> cluster_name lab1
==> cluster_type k8s          (from clusters/lab1.tfvars)
==> addons       hdfs,keycloak,opensearch  (from the ADDONS override)
==> sizing       clusters/lab1.tfvars
```

**`lab/deprovisioncluster/scripts/validate_deprov.py`** — new, its own copy
of the same three lines over `planvars`, checking `cluster_name` alone. Plan
directories stay sealed: no plan reaches into another plan's scripts. The
`_prov` / `_deprov` suffixes are not decoration — the test suite puts both
scripts directories on `sys.path`, so two files named `validate.py` would
leave one of them permanently shadowed and untestable.

When `clusters/<name>.tfvars` does not exist, validate prints
`WARNING: no clusters/lab7.tfvars — using defaults.tfvars sizing`. A
warning, not a failure: provisioning an unnamed cluster at the default size
is legitimate, and a typo in `cluster_name` is not otherwise visible until
the VMs come up the wrong size.

### Pipeline shape

`ProvisionClusterSpec` gains a first stage `Validate` holding one job. That
job carries **no `agent.role` requirement** — it needs only Python 3, so the
containerized `agent.role=ci` agent can run it, and a typo fails in seconds
even while the host agent is busy with another cluster. The existing
`Provision` stage is unchanged except that Bamboo now gates it on Validate
passing.

`DeprovisionClusterSpec` gains the same stage for `cluster_name`, and the
matching `cluster_name` placeholder default.

### CLI parity

`provision.py`, `install.py` and `deprovision.py` each call `planvars` as
their first statement, above `proc.require_tools` and above any `multipass`
call, so `make provision CLUSTER=lab1 TYPE=k9s` fails with the identical
message Bamboo prints. Validation is deliberately not duplicated into the
Makefile in shell; the existing `[ -n "$(CLUSTER)" ]` guards become
redundant but stay, being harmless.

## Testing

New `src/test/python/test_planvars.py` covers the resolution table: exact
placeholder match, near-miss `k8s|dcos` rejected, `none`, `none,hdfs`
rejected, an unknown addon named in the error text, de-duplication,
whitespace tolerance, and rejected cluster names. `test_validate_prov.py`
and `test_validate_deprov.py` cover the two entrypoints, including the
tfvars-fallback warning. `test_provision.py` gains a case that stubs
`multipass.list_vms` with a function that raises, proving validation
finishes before the backend is touched.

The `clusters_dir` fixture moves from `test_tfvars.py` to `conftest.py`,
since three test modules now resolve a cluster's tfvars file.

The two Java spec tests assert each variable's default equals the
documented placeholder. That pairing — a Java string and a Python string
that must stay identical — is the most likely thing to drift silently, so
it is the thing worth pinning.

## Documentation

`CLAUDE.md`'s `ADDONS=` line is corrected: blank falls back to the cluster's
tfvars, and `none` disables every addon.

`docs/superpowers/specs/2026-08-03-cluster-addons-design.md` still describes
a Splunk addon — three VMs, one search head, two indexers — that was never
built. OpenSearch replaced it because Splunk Enterprise ships no Linux arm64
build, as recorded in `docs/using-cluster-addons.md`. That spec gets a
superseded note pointing there, so the placeholder advertising `opensearch`
does not read as a mistake against it.

## Out of scope

Cross-validating `addons` against `cluster_type` — for instance rejecting
addons under `dcos`. No such constraint exists in the ansible roles today,
and inventing one here would be guessing at a rule nobody has hit.

Adding a Splunk addon. It has no Apple Silicon story without an emulation
path, which is a separate piece of work.
