# Infrastructure Repository

This repository contains the GitOps configuration for our Kubernetes cluster, managed by ArgoCD.

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
- **Grafana Dashboards**: Dashboards are managed as ConfigMaps generated from JSON and synced via Kustomize to prevent sidecar overwrite issues.
- **Ingress NGINX Metrics**: The NGINX Ingress controller has been patched to expose port `10254`, allowing Prometheus to scrape its metrics.

*(This README is actively maintained and will be expanded as more services are added)*
