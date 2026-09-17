# Setup

How to get forge-lab from a fresh clone to a running Bamboo with a connected
agent, on either host OS the lab has been run on:

- **macOS** (Apple Silicon) — the original host; the one the README describes.
- **Ubuntu 24.04** (x86_64) — works with the differences listed below.

The README stays the reference for *why* things are the way they are; this file
is the checklist.

## 1. Host requirements

| | macOS | Ubuntu |
| --- | --- | --- |
| CPU | Apple Silicon (VMs are arm64) | x86_64 with KVM (VMs are amd64) |
| RAM | 64G recommended | 32G runs Bamboo; see "VM budget" before provisioning |
| Virtualisation | built in | `/dev/kvm` read/write for your user |

Check KVM on Ubuntu — both tests must pass:

```bash
[ -r /dev/kvm ] && [ -w /dev/kvm ] && echo "kvm ok"
```

If not, add yourself to the group and log out and back in:

```bash
sudo usermod -aG kvm "$USER"
```

## 2. Install tools

Needed on `PATH`: `kubectl`, `helm`, `terraform`, `ansible-playbook`,
`ansible-lint`, `multipass`, `java` (21), `mvn`, `uv`, `pytest`, `jq`, `xxd`,
`openssl`, `curl`, `python3`. Rancher Desktop must be installed and running.

### macOS

```bash
brew install --cask rancher multipass temurin@21
brew install helm terraform ansible ansible-lint jq maven
uv tool install pytest
```

### Ubuntu

Two installs need `sudo` — Rancher Desktop and Multipass have no user-space
option:

```bash
# Rancher Desktop (openSUSE build service apt repo, per Rancher's Linux docs)
curl -s https://download.opensuse.org/repositories/isv:/Rancher:/stable/deb/Release.key \
  | gpg --dearmor | sudo tee /usr/share/keyrings/isv-rancher-stable-archive-keyring.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/isv-rancher-stable-archive-keyring.gpg] https://download.opensuse.org/repositories/isv:/Rancher:/stable/deb/ ./' \
  | sudo tee /etc/apt/sources.list.d/isv-rancher-stable.list >/dev/null
# apt verifies signatures as the unprivileged user `_apt`, which must be able to
# read both files. Writing them under a 077 umask leaves them 0600 root-only and
# `apt update` then fails with "NO_PUBKEY 1F833FA1A63FE758", which reads like a
# missing key rather than a permission problem. Set the mode explicitly:
sudo chmod 644 /usr/share/keyrings/isv-rancher-stable-archive-keyring.gpg \
               /etc/apt/sources.list.d/isv-rancher-stable.list
sudo apt update && sudo apt install -y rancher-desktop

# Multipass
sudo snap install multipass

# Ansible and friends, if missing
sudo apt install -y ansible jq xxd curl
```

The rest installs into `~/.local` without `sudo`. `~/.local/bin` must be on
`PATH`. `uv` installs the Python-side tools and installs itself the same way:

```bash
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

mkdir -p ~/.local/bin ~/.local/opt && cd "$(mktemp -d)"

# kubectl
v=$(curl -fsSL https://dl.k8s.io/release/stable.txt)
curl -fsSLo kubectl "https://dl.k8s.io/release/$v/bin/linux/amd64/kubectl"
install -m755 kubectl ~/.local/bin/

# helm
v=$(curl -fsSL https://api.github.com/repos/helm/helm/releases/latest | jq -r .tag_name)
curl -fsSL "https://get.helm.sh/helm-$v-linux-amd64.tar.gz" | tar xz
install -m755 linux-amd64/helm ~/.local/bin/

# terraform
v=$(curl -fsSL https://checkpoint-api.hashicorp.com/v1/check/terraform | jq -r .current_version)
curl -fsSLo tf.zip "https://releases.hashicorp.com/terraform/$v/terraform_${v}_linux_amd64.zip"
python3 -c "import zipfile; zipfile.ZipFile('tf.zip').extract('terraform')"
install -m755 terraform ~/.local/bin/

# JDK 21 (Temurin). 21, not 17: the Bamboo 12.1.8 agent installer jar is class
# file version 65, so a 17 runtime rejects it with UnsupportedClassVersionError
# at `make agent-install`. The server pod runs Temurin 21 too.
url=$(curl -fsSL "https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=linux&vendor=eclipse" \
  | jq -r '.[0].binary.package.link')
mkdir -p ~/.local/opt/jdk-21 && curl -fsSL "$url" | tar xz -C ~/.local/opt/jdk-21 --strip-components=1
ln -sf ~/.local/opt/jdk-21/bin/{java,javac,jar} ~/.local/bin/

# Maven
v=3.9.16
mkdir -p ~/.local/opt/maven && curl -fsSL \
  "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/$v/apache-maven-$v-bin.tar.gz" \
  | tar xz -C ~/.local/opt/maven --strip-components=1
ln -sf ~/.local/opt/maven/bin/mvn ~/.local/bin/

# Python-side tools
uv tool install pytest
uv tool install ansible-lint
```

### Both: ansible collections

`ansible-lint` runs in its own venv and does not see the collections bundled
with the system `ansible` (Homebrew on macOS, apt on Ubuntu). Install them into
the user path both tools search. `--force` is needed on Ubuntu, where
`ansible-galaxy` otherwise reports the apt copies as "already installed" and
does nothing:

```bash
ansible-galaxy collection install --force -p ~/.ansible/collections \
  community.general ansible.posix
```

### Check

```bash
for bin in kubectl helm terraform ansible-playbook ansible-lint multipass java mvn \
           uv pytest jq xxd openssl curl python3; do
  command -v "$bin" >/dev/null || echo "MISSING: $bin"
done
java -version   # expect 21.x
make lint       # pytest + terraform + ansible-lint + mvn test — must pass
```

`make lint` needs no cluster, so it is the first proof the clone works.

## 3. Start Rancher Desktop

Open Rancher Desktop and enable Kubernetes. In Preferences → Virtual Machine,
give it at least **4 CPUs and 6G memory**: Bamboo requests 1 CPU / 3Gi and is
capped at 2 CPU / 4Gi, and Postgres wants another 512Mi.

Wait until Kubernetes shows as running, then point `kubectl` at it:

```bash
kubectl config use-context rancher-desktop
kubectl get nodes     # one node, Ready
```

## 4. Bring up Bamboo

```bash
make bootstrap
```

One command: namespace `ci`, secrets (DB password, 24h licence, `admin/admin`,
agent token), the lab SSH key at `~/.forgelab/id_ed25519`, Postgres, Bamboo, and
Bamboo's unattended setup. First boot builds the schema; allow up to 10 minutes.

Then, in a terminal you leave open:

```bash
make ui     # port-forwards 8085 (UI) and 54663 (agent broker)
```

Open <http://localhost:8085> and log in as `admin` / `admin`.

## 5. Connect the host agent

In a second terminal, while `make ui` is still running:

```bash
make agent-install   # copies the agent jar out of the Bamboo pod
make agent-run       # leave running; plans build only while it is up
```

Approve the agent once: Administration → Agents → Agent authentication →
approve the new UUID. The `agent-run` log prints the approval URL.

## 6. Publish the plans

Both steps below are one-time and both must be done **before**
`make specs-publish`. The publisher walks the plans in alphabetical order, so a
missing linked repository lets `AGENT-BUILD` publish and then fails on
`FORGE-DEPROV` — the first plan that references the repo.

### 6a. Personal access token

Bamboo 12 disables HTTP basic authentication (`Basic Authentication has been
disabled on this instance.`), so a token is the only way the publisher can
authenticate.

The page is under your own profile, not Administration: avatar, top right →
**Profile** → **Personal access tokens** tab → **Create token**. Direct link,
while `make ui` is running:

<http://localhost:8085/profile/userAccessTokens.action>

Name it anything; it inherits your permissions, so `admin`'s token can publish.
Copy the value at creation — Bamboo never shows it again. Then save it where
the publisher looks:

```bash
mkdir -p ~/.forgelab
install -m600 /dev/null ~/.forgelab/bamboo_pat
echo '<your PAT>' > ~/.forgelab/bamboo_pat
```

`FORGELAB_BAMBOO_PAT` overrides the file for a single run. The publisher
generates `bamboo-specs/.credentials` from whichever it finds; never write that
file by hand.

### 6b. Linked repository

`ProvisionClusterSpec`, `DeprovisionClusterSpec` and `PublishSpecsSpec` all
resolve their checkout by *name* — `SpecConstants.REPO_NAME`, the string
`forge-lab`. Bamboo will not create it for you, and a name that differs by even
a capital letter fails the same way.

Administration (gear, top right) → **Build resources** → **Linked
repositories** → **Add repository**. Direct link:

<http://localhost:8085/admin/configureLinkedRepositories.action>

The first choice is the repository host, and **it must be `Git`** — the plain
one, near the bottom of the list. Picking `Bitbucket DC / Server` (it sits at
the top and looks like the obvious choice) replaces the URL field with
"Bitbucket Server application link required" and pushes you into a *Create
link* OAuth dialog. Nothing here needs an application link; if you see one,
cancel and start again with `Git`. `GitHub` is also the wrong choice — the
specs build a `GitRepository`, so the linked repository has to be the same
type.

| Field | Value |
| --- | --- |
| Repository host | `Git` — not Bitbucket, not GitHub |
| Repository name | `forge-lab` — exactly this, it is matched by name |
| Repository URL | `https://github.com/r0jjames/forge-lab.git` |
| Branch | `main` |
| Authentication type | `None` |

The repository is public, so HTTPS needs no credentials and nothing has to be
put on the server. Use the SSH URL `git@github.com:r0jjames/forge-lab.git`
instead only if you work from a private fork — that needs a key pair whose
public half is a deploy key on the fork and whose private half is pasted into
this form; `~/.forgelab/id_ed25519` is the *cluster VM* key and is not
registered with GitHub.

Save, then **Test connection** on the saved repository before publishing.

`BuildAgentImageSpec` needs none of this: it declares a plan-local repository
for the public `bamboo-agent` repo, which is why `AGENT-BUILD` publishes even
when this step is missing.

### 6c. Publish

```bash
make specs-publish
make hooks-install     # pushes to main now republish every plan
```

Re-running `make specs-publish` after a failure is safe — publishing a plan is
idempotent, so the plans that already landed are simply republished.

## 7. Provision a cluster

Check the size first — this runs in seconds and needs no VMs:

```bash
bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py lab1
```

Then run the **Provision** plan from Bamboo, or directly:

```bash
make provision CLUSTER=lab1
```

See `docs/provision-usage.md` for plan variables and health checks.

### VM budget

Every shipped config is sized for a 64G Mac:

| Config | VMs | Guest RAM |
| --- | --- | --- |
| `lab1` | 11 | 48G |
| `opensearch1` | 10 | 44G |
| `dcos1` | 8 | 34G |
| `splunk1` | 11 | 48G |

On a 32G host none of them fit alongside Rancher Desktop. Copy one to a new
`cluster_configs/<name>_cluster.yaml`, turn off technologies
(`enabled: false`) or lower counts until `validate_prov.py` reports a total that
leaves room for Rancher Desktop and the desktop, then provision that name.

## Daily use

| Symptom | Fix |
| --- | --- |
| Bamboo says "license has expired ... read-only mode" | `make bamboo-restart`, then `make ui` again |
| Boot fails with "Shared configuration ... does not exist" | `make reset` (destructive: wipes Bamboo PVCs and DB) |
| Agent cannot reach the broker | `make ui` is not running |
| `specs-publish` fails with `Linked repository 'forge-lab' does not exist` | step 6b was skipped, or the name is not exactly `forge-lab` |
| Adding the linked repository demands a Bitbucket application link | repository host is `Bitbucket DC / Server`; it must be `Git` |
| `specs-publish` fails with `Couldn't find credentials file: .credentials` | no PAT — see step 6a |
| `make agent-install` fails with `UnsupportedClassVersionError ... class file version 65.0` | `java` is 17; the 12.1.8 agent needs 21 |
| `helm` fails with `docker-credential-secretservice ... not found in $PATH` | `~/.rd/bin` is missing from `PATH`; start a new shell, or `export PATH="$HOME/.rd/bin:$PATH"` |
| Want to see pods | `make status` |

## macOS vs Ubuntu: what differs

- **VM architecture.** Multipass VMs match the host: arm64 on Apple Silicon,
  amd64 on Ubuntu x86_64. The roles pick the right download for both. On
  Ubuntu, Splunk Enterprise runs natively — the `qemu-user` emulation tasks
  only run when `ansible_architecture == "aarch64"`, so the ~2 minute start and
  ~5x search slowdown described in the README do not apply.
- **Clipboard.** `make license` copies the key with `pbcopy` on macOS. Ubuntu
  has no `pbcopy`, so the key is printed only. `make bootstrap` does not need
  the clipboard.
- **Opening URLs.** `make relicense` calls `open`, which on Ubuntu is an
  alternative for `xdg-open`, so it works on both.
- **Tool install location.** Rancher Desktop puts its own `kubectl`/`helm` in
  `~/.rd/bin` on both OSes. The Ubuntu steps above install separate copies in
  `~/.local/bin`; either copy works for `kubectl` and `helm`. `~/.rd/bin` must
  be on `PATH` regardless, because it also holds
  `docker-credential-secretservice`, the helper `~/.docker/config.json` names
  as its `credsStore`. Helm pulls the Postgres chart from Docker Hub over OCI
  and runs that helper, so without the directory on `PATH` `make bootstrap`
  fails at the first `helm upgrade` with
  `exec: "docker-credential-secretservice": executable file not found in $PATH`.
  Rancher Desktop adds the line to `~/.profile` itself, but only shells started
  afterwards pick it up — an editor or terminal left open from before keeps the
  old `PATH`.
- **Multipass socket.** On Ubuntu the snap grants the socket to the `sudo`
  group; a user outside it gets permission errors from `multipass list`.
