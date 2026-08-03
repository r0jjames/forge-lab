# cluster_registered/

One `<cluster>_cluster_info.yml` per live lab cluster: node addresses, sizing,
ssh hints, and the components ansible installed.

Generated, not hand-written:

- `make provision CLUSTER=lab1` (or the FORGE-PROV plan) writes the file as its
  last step, after verify passes
- `make deprovision CLUSTER=lab1` deletes it

Nothing commits for you. The files are tracked, so a provision or teardown shows
up in `git status` and you commit it when you want that cluster recorded in
history. The PROV plan also publishes the file as the `cluster-info` artifact.

Plans run from Bamboo's own per-plan checkout, not from this clone, so the
location is resolved as `$FORGELAB_REGISTRY_DIR`, then `~/.forgelab/registry_dir`
(one line, written by `make agent-run`), then the checkout itself. Without that
PROV would write into its build directory and DEPROV — a different build
directory — could never clean it up.

The addresses are multipass DHCP leases on your own machine and go stale the
moment the VMs are gone — the file describes a cluster, it does not keep it.
