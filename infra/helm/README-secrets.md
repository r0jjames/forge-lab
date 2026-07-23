# Secrets

This stack does not commit any secrets to the repo. Two things must exist
in the cluster (namespace `ci`) before Bamboo will come up healthy:

## 1. `bamboo-db-creds` (Kubernetes Secret)

Created automatically by `make setup` — see the `setup` target in the
`Makefile`. It generates a random password with `openssl rand -hex 16`
and stores it alongside the fixed username:

```
kubectl -n ci create secret generic bamboo-db-creds \
  --from-literal=username=bamboo \
  --from-literal=password="$(openssl rand -hex 16)"
```

Keys:
- `username` — always `bamboo`
- `password` — random, generated once; `make setup` is idempotent and
  will not overwrite an existing secret

Both Postgres (`infra/helm/postgres-values.yaml`, via `auth.existingSecret`)
and Bamboo (`infra/helm/bamboo-values.yaml`, via `database.credentials.secretName`)
reference this same secret so they always agree on credentials.

To inspect (never commit the output):

```
kubectl -n ci get secret bamboo-db-creds -o jsonpath='{.data.password}' | base64 -d
```

## 2. Bamboo license key (manual, human-only)

Bamboo requires a license key entered through the setup wizard on first
boot (`http://localhost:8085` after `make ui`). For local/personal labs,
use a free timebomb (evaluation) license from the Atlassian developer
site:

https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/

(Requires a free Atlassian account. Generate a Bamboo Data Center
evaluation/timebomb license via https://my.atlassian.com, then paste it
into the setup wizard when prompted.)

This key is never written to disk in this repo and is not stored by any
Makefile target — it lives only in the running Bamboo instance's data
volume / database.

## 3. SSH keypair

`make setup` also generates a local ed25519 keypair at
`~/.forgelab/id_ed25519` (outside the repo) if one does not already
exist, for future agent-side use. It is not a Kubernetes secret and is
not committed.
