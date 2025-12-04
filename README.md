# kube-dump

Backup Kubernetes cluster resources as clean, reusable YAML manifests.

## About

This project is a **Python reimplementation** inspired by [WoozyMasta/kube-dump](https://github.com/WoozyMasta/kube-dump). The original bash-based tool is excellent, but hasn't been actively maintained. This Python version was created to:

- Provide continued maintenance and updates
- Add new features like **Slack notifications**
- Improve cross-platform compatibility
- Make the codebase more maintainable and extensible

**Credits:** Original concept and design by [WoozyMasta](https://github.com/WoozyMasta).

## Features

- 📦 **Dump all Kubernetes resources** - namespaced and cluster-wide
- 🧹 **Clean YAML output** - removes runtime fields (`uid`, `resourceVersion`, `managedFields`, etc.)
- ✅ **Valid manifests** - includes `apiVersion` and `kind` for kubectl apply compatibility
- 🔄 **Git integration** - auto-commit and push backups to a remote repository
- 📁 **Archive support** - create compressed archives (gz, bz2, xz) with rotation
- 🔔 **Slack notifications** - get notified on backup success or failure
- 🐳 **Docker support** - run in containers or Kubernetes CronJobs
- 🔍 **Auto-discovery** - automatically discovers all API resources

## Installation

### Using Docker

```bash
docker pull devopstales/kube-dump:latest
```

## Usage

### Basic Usage

```bash
# Dump all resources
./kube-dump.py dump

# Dump only namespaced resources
./kube-dump.py dump-namespaces

# Dump only cluster-wide resources  
./kube-dump.py dump-cluster

# Dump specific namespaces
./kube-dump.py dump -n default,kube-system
```

### Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `all` | `dump` | Dump all kubernetes resources |
| `ns` | `dump-namespaces` | Dump namespaced resources only |
| `cls` | `dump-cluster` | Dump cluster-wide resources only |

### CLI Options

| Flag | Environment Variable | Default | Description |
|------|---------------------|---------|-------------|
| `-h, --help` | | | Show help message |
| `-s, --silent` | `SILENT` | `false` | Suppress stdout messages |
| `-d, --destination-dir` | `DESTINATION_DIR` | `./data` | Output directory for dumps |
| `-f, --force-remove` | `FORCE_REMOVE` | `false` | Remove destination before dump |
| `--detailed` | `DETAILED` | `false` | Keep detailed fields (status, etc.) |
| `-n, --namespaces` | `NAMESPACES` | all | Comma-separated list of namespaces |
| `--kube-config` | `KUBE_CONFIG` | auto | Path to kubeconfig file |
| `--kube-context` | `KUBE_CONTEXT` | current | Kubeconfig context to use |

### Git Options

| Flag | Environment Variable | Default | Description |
|------|---------------------|---------|-------------|
| `-c, --git-commit` | `GIT_COMMIT` | `false` | Commit changes to git |
| `-p, --git-push` | `GIT_PUSH` | `false` | Push commits to remote |
| `-b, --git-branch` | `GIT_BRANCH` | `main` | Git branch name |
| `--git-commit-user` | `GIT_COMMIT_USER` | `kube-dump` | Git commit author name |
| `--git-commit-email` | `GIT_COMMIT_EMAIL` | `kube-dump@example.com` | Git commit author email |
| `--git-remote-url` | `GIT_REMOTE_URL` | | Remote repository URL (with credentials) |

### Archive Options

| Flag | Environment Variable | Default | Description |
|------|---------------------|---------|-------------|
| `-a, --archive` | `ARCHIVE` | `false` | Create compressed archive |
| `--archive-type` | `ARCHIVE_TYPE` | `gz` | Archive type: `gz`, `bz2`, `xz` |
| `--archive-rotate-days` | `ARCHIVE_ROTATE_DAYS` | `7` | Delete archives older than N days |

### Slack Options

| Flag | Environment Variable | Default | Description |
|------|---------------------|---------|-------------|
| `--cluster-name` | `CLUSTER_NAME` | `unknown` | Cluster name for notifications |
| `--slack-url` | `SLACK_URL` | | Slack webhook URL |
| `--slack-channel` | `SLACK_CHANNEL` | `#alerts` | Slack channel for notifications |

## Examples

### Local backup with archiving

```bash
./kube-dump.py dump \
  --destination-dir ./backup \
  --archive \
  --archive-type gz \
  --archive-rotate-days 30
```

### Backup with Git push

```bash
export GIT_REMOTE_URL="https://user:token@github.com/org/k8s-backups.git"

./kube-dump.py dump \
  --destination-dir ./backup \
  --git-commit \
  --git-push \
  --git-branch main
```

### Backup with Slack notifications

```bash
export SLACK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"
export CLUSTER_NAME="production-cluster"

./kube-dump.py dump \
  --destination-dir ./backup \
  --slack-channel "#k8s-backups"
```

### Full example with all features

```bash
./kube-dump.py dump \
  --destination-dir ./backup \
  --namespaces default,kube-system,monitoring \
  --archive \
  --archive-type gz \
  --git-commit \
  --git-push \
  --git-remote-url "https://user:token@gitlab.com/backups/k8s.git" \
  --cluster-name "prod-cluster" \
  --slack-url "https://hooks.slack.com/services/XXX" \
  --slack-channel "#alerts"
```

## Docker Usage

### Run with kubeconfig

```bash
docker run --rm \
  -v ~/.kube/config:/root/.kube/config:ro \
  -v /path/to/backup:/data/kube-dump \
  devopstales/kube-dump:latest
```

### Run with all options

```bash
docker run --rm \
  -v ~/.kube/config:/root/.kube/config:ro \
  -v /path/to/backup:/data/kube-dump \
  -e CLUSTER_NAME=production \
  -e SLACK_URL="https://hooks.slack.com/services/XXX" \
  -e SLACK_CHANNEL="#backups" \
  -e GIT_COMMIT=true \
  -e GIT_PUSH=true \
  -e GIT_REMOTE_URL="https://user:token@github.com/org/backup.git" \
  devopstales/kube-dump:latest
```

### Helm Chart

```bash
# Install with Helm
helm install kube-dump ./chart \
  --namespace kube-dump \
  --create-namespace \
  --set config.clusterName=my-cluster \
  --set git.remoteUrl="https://user:token@github.com/org/backup.git" \
  --set slack.enabled=true \
  --set slack.webhookUrl="https://hooks.slack.com/services/XXX"
```

Or with a values file:

```bash
helm install kube-dump ./chart \
  --namespace kube-dump \
  --create-namespace \
  -f my-values.yaml
```

See `chart/values.yaml` for all configuration options.

### Kustomize / kubectl

Ready-to-use Kubernetes manifests are available in the `deploy/` folder:

```bash
# Review and edit the secret with your credentials
vim deploy/secret.yaml

# Apply with kubectl
kubectl apply -f deploy/

# Or use kustomize
kubectl apply -k deploy/
```

**Included manifests:**
- `serviceaccount.yaml` - ServiceAccount for kube-dump
- `clusterrole.yaml` - ClusterRole with read permissions for all resources
- `clusterrolebinding.yaml` - Binds the ClusterRole to the ServiceAccount
- `secret.yaml` - Secret for Git and Slack credentials (edit before applying!)
- `pvc.yaml` - PersistentVolumeClaim for backup data
- `cronjob.yaml` - CronJob running daily at 2:00 AM
- `kustomization.yaml` - Kustomize configuration

## Output Structure

```
backup/
├── cluster/
│   ├── namespaces/
│   │   ├── default.yaml
│   │   ├── kube-system.yaml
│   │   └── ...
│   ├── clusterroles/
│   ├── clusterrolebindings/
│   └── ...
├── namespaces/
│   ├── default/
│   │   ├── configmaps/
│   │   ├── deployments/
│   │   ├── services/
│   │   └── ...
│   ├── kube-system/
│   │   └── ...
│   └── ...
└── backup-20231201-120000.tar.gz  (if archiving enabled)
```

## Development

### Using Poetry (recommended for development)

```bash
git clone https://github.com/devopstales/kube-dump.git
cd kube-dump
poetry install
```

### Using pip

```bash
pip install -r requirements.txt
```

### Using Task

```bash
# Install dependencies
task install

# Run locally
task run

# Run for specific namespaces
task run-ns NAMESPACES=default,monitoring

# Lint code
task lint

# Build Docker image
task docker-build VERSION=1.0.0
```

## License

This project is licensed under the GNU General Public License v2.0 - see the original [WoozyMasta/kube-dump](https://github.com/WoozyMasta/kube-dump) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Related Projects

- [WoozyMasta/kube-dump](https://github.com/WoozyMasta/kube-dump) - Original bash implementation

