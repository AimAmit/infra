# Infrastructure Repository

This repository is the **single source of truth** for the entire Kubernetes cluster. Everything is managed via GitOps through ArgoCD.

## 🚀 Bootstrap (Fresh Cluster)

To deploy the entire infrastructure on a fresh Kubernetes (K3s) cluster, run these commands:

```bash
# 1. Install K3s
curl -sfL https://get.k3s.io | sh -

# 2. Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# 3. Bootstrap everything (single command)
kubectl apply -f https://raw.githubusercontent.com/AimAmit/infra/HEAD/bootstrap/root-app.yaml
```

That's it. The `root-app` uses the **App-of-Apps pattern** — it watches `bootstrap/apps/`, which contains all ArgoCD Application definitions + the StorageClass. ArgoCD cascades and creates all services automatically.

> **Note:** The Application YAMLs in `bootstrap/apps/` are copies of the `argocd-application.yaml` files from each service directory. When updating an app's ArgoCD configuration, update both the source file in the service directory and the copy in `bootstrap/apps/`.

### What gets created

The root app syncs the following automatically:

| Service | Type | Source |
|---------|------|--------|
| AdGuard Home | Kustomize | `cluster/adguard/` |
| Browserless | Kustomize | `cluster/browserless/` |
| Hermes Agent | Kustomize | `cluster/hermes/` |
| Prometheus Stack | Helm (multi-source) | `cluster/prometheus-stack/` |
| Grafana Dashboards | Kustomize | `cluster/prometheus-stack/dashboards/` |
| Redis | Helm (multi-source) | `cluster/redis/` |
| Tailscale Operator | Helm | `cluster/tailscale/` |
| Icon Generator | Helm | `projects/icon-generator--embedding/` |
| Typeahead-rs | Helm | `projects/typeahead-rs/` |
| StorageClass | Raw YAML | `cluster/storage/storageclass.yaml` |

### Adding a new service

1. Create your manifests in `cluster/<service-name>/` or `projects/<service-name>/`
2. Add an `argocd-application.yaml` in that directory
3. Reference it in `bootstrap/kustomization.yaml`
4. Commit and push — ArgoCD picks it up automatically

## 📁 Repository Structure

```
infra/
├── bootstrap/                    # App-of-Apps (entry point)
│   ├── root-app.yaml             # The single bootstrapping Application
│   └── kustomization.yaml        # References all argocd-application.yaml files
├── cluster/                      # Cluster services
│   ├── adguard/                  # DNS ad-blocking
│   ├── browserless/              # Headless browser
│   ├── hermes/                   # Remote execution agent
│   ├── ingress/                  # NGINX Ingress controller
│   ├── metallb/                  # Bare-metal load balancer
│   ├── prometheus-stack/         # Monitoring (Prometheus + Grafana)
│   │   ├── storage/              # PVs for Prometheus & Grafana
│   │   └── dashboards/           # Grafana dashboard ConfigMaps
│   ├── redis/                    # Redis cache
│   │   └── storage/              # PV for Redis
│   ├── storage/                  # Shared StorageClass definition
│   └── tailscale/                # VPN / secure networking
└── projects/                     # Application workloads
    ├── icon-generator--embedding/
    └── typeahead-rs/
```

## 💾 Storage Architecture

All persistent data lives on a dedicated OCI block volume mounted at `/mnt/oci-block-volume/` on the node. Each service owns its PersistentVolume definition:

| Service | PV Path | Managed By |
|---------|---------|------------|
| Redis | `/mnt/oci-block-volume/redis` | `cluster/redis/storage/redis-pv.yaml` |
| Prometheus | `/mnt/oci-block-volume/prometheus` | `cluster/prometheus-stack/storage/prometheus-pv.yaml` |
| Grafana | `/mnt/oci-block-volume/grafana` | `cluster/prometheus-stack/storage/grafana-pv.yaml` |
| Hermes | `/mnt/oci-block-volume/hermes` | `cluster/hermes/pv.yaml` |
| AdGuard | `/mnt/oci-block-volume/adguard-work` | `cluster/adguard/pv.yaml` |

**Principle:** Static configuration → ConfigMaps. Growing data → PersistentVolumes on the block volume.

## Cluster Components

### Core Infrastructure
- **ArgoCD**: The GitOps engine syncing this repository to the cluster.
- **Tailscale Operator**: Manages Tailscale integration, allowing secure remote access to cluster services and exposing local machines to the cluster via Egress Proxies.

### Observability & Monitoring
- **Prometheus Stack (`kube-prometheus-stack`)**: Collects cluster metrics, Node metrics, and application metrics.
- **Grafana**: Visualizes metrics via provisioned dashboards. We have customized this by disabling the massive default bundle and individually provisioning high-value dashboards (Cluster Monitoring, Node Exporter, NGINX Ingress, Kubernetes Pods/Views).

### Applications & Services
- **Hermes Agent**: A remote execution agent running in the cluster. It is configured to securely communicate with the local Mac environment using SSH over a Tailscale Egress Proxy (`mac-ssh`), meaning all terminal commands are end-to-end encrypted and routed over WireGuard.
- **Browserless**: A headless browser instance used by Hermes for web automation tasks.
- **AdGuard Home**: Network-wide ad blocking and DNS routing.
- **Typeahead-rs & Icon-generator-embedding**: Additional internal services.

## Recent Configurations
- **Hermes SSH Integration**: Hermes is configured with `terminal.backend = ssh`. The SSH private key is injected securely via a Kubernetes Secret (bypassing Git) and the connection routes through the Tailscale Egress Proxy pod to the local Mac.
- **Hermes Token Optimization**: We implemented the strict delegation pattern by trimming the primary agent's tools to just orchestrator components (`[delegation, clarify, session_search, todo, memory, skills]`). Memory compaction threshold has been tightened (from 0.5 to 0.2) reducing baseline token footprint.
- **Grafana Dashboards**: Dashboards are managed as ConfigMaps generated from JSON and synced via Kustomize to prevent sidecar overwrite issues.
- **Ingress NGINX Metrics**: The NGINX Ingress controller has been patched to expose port `10254`, allowing Prometheus to scrape its metrics.

*(This README is actively maintained and will be expanded as more services are added)*
