"""forgelab — the lab's one Python library.

Shared by every plan's entrypoints under `lab/<planid>/scripts/` and by the
host-only scripts under `infra/`. The dependency direction is one-way:
`infra/` and `lab/<planid>/` may import `forgelab`; `forgelab` imports neither,
and no plan reaches into another plan's directory.

Standard library only — these scripts run on the host agent with nothing
installed beyond the CLI tools they drive (terraform, multipass, ansible,
kubectl, ssh, java).
"""
