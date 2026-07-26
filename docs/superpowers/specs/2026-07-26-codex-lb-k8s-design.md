# codex-lb on Kubernetes, tailnet-only

Date: 2026-07-26
Status: pending review

## Summary

Deploy [codex-lb](https://github.com/Soju06/codex-lb) into the cluster as an ArgoCD-managed
application, reachable only from the tailnet. No public ingress, no public IP binding.

## What codex-lb is (and is not)

codex-lb pools **ChatGPT / Codex OAuth accounts** and load-balances requests across them. Its
upstream is `https://chatgpt.com/backend-api` (`CODEX_LB_UPSTREAM_BASE_URL` default).

It is **not** a proxy for the OpenAI platform API:

- Accounts are OAuth sign-ins. There is no slot for a platform `sk-proj-...` key.
- Served models come from the Codex catalog (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-5.5`, `gpt-5.4`). No embeddings, audio, or image models.
- `/v1` is an OpenAI-shaped façade over Codex, not a passthrough to `api.openai.com`.

Consequence for this cluster: codex-lb serves coding-agent traffic backed by ChatGPT
subscriptions. Platform-API traffic keeps its existing path — `cluster/hermes` already points
`OPENAI_API_KEY` at a Cloudflare AI Gateway token, and that is unchanged by this work.

Endpoints exposed by the service:

| Path | Consumer |
| --- | --- |
| `/backend-api/codex` | Codex CLI, Codex IDE extension |
| `/v1` | OpenCode, OpenClaw, hermes, OpenAI SDKs |

Ports: `2455` dashboard + proxy, `1455` OAuth login callback.

## Architecture

Single-replica Deployment in namespace `codex-lb`, SQLite on a PersistentVolumeClaim, fronted by
a ClusterIP Service that the Tailscale operator publishes to the tailnet.

```
tailnet device
      |
      v
codex-lb.tail94c55.ts.net:2455        (operator-managed proxy pod, tailnet-only)
      |
      v
Service codex-lb (ClusterIP) :2455 :1455
      |
      v
Deployment codex-lb (1 replica)
      |
      +-- PVC codex-lb-data 1Gi -> /var/lib/codex-lb
      |
      v
https://chatgpt.com/backend-api       (upstream, via node egress)
```

### Components

**Deployment `codex-lb`** — image pinned to digest, since the `1.22.0` tag is mutable on ghcr:

```
ghcr.io/soju06/codex-lb:1.22.0@sha256:3010b3c5567522fbb6e7225bdbe963fa1038c2584641fcec69094875e40e36e5
```

One replica, `strategy: Recreate` because SQLite sits on a ReadWriteOnce volume and the old pod
must release the mount first. Container listens on 2455 (entrypoint pins `--port 2455`); 1455 is
bound only while an OAuth account-add is in flight.

Hardened to match upstream's own chart defaults, which are known to work with this image:

- pod: `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`,
  `seccompProfile: RuntimeDefault`. The UID must be explicit — the image's `USER app` is a name,
  not a number, so `runAsNonRoot` alone would fail kubelet's non-root verification.
- container: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`,
  `readOnlyRootFilesystem: true`.
- `automountServiceAccountToken: false` — the pod never contacts the API server.
- emptyDir mounts at `/tmp` and `/app/.cache`, required by the read-only root filesystem.
- resources: requests 100m / 256Mi, limits 1000m / 1Gi.
- probes: `/health/startup`, `/health/ready`, `/health/live` on the http port.

No environment variables and no Secret. Every setting this deployment needs is already the
upstream default:

| Considered | Decision | Reason |
| --- | --- | --- |
| `CODEX_LB_ENCRYPTION_KEY_FILE` | omit | default location is the data dir, which is the PVC |
| `CODEX_LB_DASHBOARD_AUTH_MODE` | omit | `standard` is the default |
| `CODEX_LB_LEADER_ELECTION_ENABLED` | omit | default `true`; a single replica elects itself |
| `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` | omit | first run happens over port-forward, which is localhost and bypasses bootstrap |
| `CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS` | omit | no reverse proxy in the path; leaving it `false` removes a header-spoofing surface |
| `CODEX_LB_DATABASE_URL` | omit | SQLite is sufficient for one user; Postgres only matters for multi-replica |

Everything else is configured in the dashboard at runtime and persisted to the PVC.

**PVC `codex-lb-data`** — 1Gi, `ReadWriteOnce`, default `local-path` StorageClass, mounted at
`/var/lib/codex-lb`. Holds:

- `encryption.key` — encrypts stored OAuth tokens. Losing it makes every account unreadable.
- `store.db` — accounts, API keys, rate limits, request logs, usage rollups.
- archives — usage rollups, the only component that grows without bound.

Persistence is mandatory, not an optimization: without it, a pod restart wipes accounts and the
dashboard password. local-path does not reserve capacity, so 1Gi is a cap rather than an
allocation. PVCs do not resize in place under ArgoCD prune/selfHeal — changing this later means
recreating the volume.

Carries `argocd.argoproj.io/sync-options: Prune=false`. With `prune: true` on the Application, a
future edit that dropped `pvc.yaml` from the repo would otherwise delete the volume and with it
`encryption.key`, orphaning every pooled account irrecoverably.

**Service `codex-lb`** — `ClusterIP`, **port 2455 only**, carrying the Tailscale operator
annotations:

```yaml
annotations:
  tailscale.com/expose: "true"
  tailscale.com/hostname: "codex-lb"
```

This is the same mechanism `cluster/browserless/service.yaml` uses. The operator provisions a
proxy pod joined to the tailnet; no node port and no public IP are involved.

Port 1455 is deliberately **excluded**. `tailscale.com/expose` publishes every port in the spec,
so listing 1455 would make the OAuth callback permanently tailnet-reachable for no benefit.
First-run account-add uses a port-forward to the Deployment, which does not require a Service
port.

`tailscale.com/funnel` must never appear on this Service. Funnel publishes to the public
internet, and this dashboard controls every pooled ChatGPT account.

**Tailnet ACL (follow-up, not in this repo).** Without a distinct tag, any tailnet device can
reach the dashboard. Restricting it requires a tailnet policy edit first —
`"tagOwners": {"tag:codex-lb": ["tag:k8s-operator"]}` plus a grant scoping `tag:codex-lb` to your
own devices — and only then uncommenting `tailscale.com/tags: "tag:codex-lb"` in the Service.
Annotating with a tag that has no `tagOwners` entry makes the operator's proxy fail to
authenticate, so the order matters. Shipped commented out for that reason; no other service in
this cluster uses tags today.

**ArgoCD wiring** — `bootstrap/apps/codex-lb.yaml` in the app-of-apps, plus
`cluster/codex-lb/argocd-application.yaml`, both matching the hermes shape: `project: default`,
`repoURL: https://github.com/AimAmit/infra.git`, `path: cluster/codex-lb`, `targetRevision: HEAD`,
`syncPolicy.automated` with `prune: true` and `selfHeal: true`, and `CreateNamespace=true`.

## Why kustomize rather than the upstream Helm chart

ArgoCD manages the app either way, with identical selfHeal and prune behavior. The choice only
determines what ArgoCD renders.

Kustomize wins here because it matches the existing hermes and browserless pattern, avoids the
chart's bitnami postgresql subchart, keeps the full rendered output in git, and reduces upgrades
to a one-line image tag bump. The chart's value — multi-replica session-bridge wiring, Gateway
API routes, external database — is all for scenarios this deployment does not have.

## Security posture

The dashboard grants full control over pooled ChatGPT accounts, so exposure is layered:

1. **No public path.** Service is `ClusterIP`. No Ingress, no HTTPRoute, no cert-manager
   certificate, no LoadBalancer, no NodePort, no Funnel. `cluster/ingress` is not touched by this
   work. Relevant because the cluster runs MetalLB, so a `LoadBalancer` type would take a real
   address.
2. **Tailnet-only reachability** via the operator annotation, 2455 only.
3. **Dashboard auth** in `standard` mode — password plus TOTP. TOTP is enabled during first-run
   setup, not left for later.
4. **API key auth** enabled for proxy routes. codex-lb rejects non-local proxy requests until
   this is configured, so tailnet clients require it regardless. Each client gets its own
   `sk-clb-...` key so keys can be revoked individually.
5. **Workload hardening** — non-root, all capabilities dropped, read-only root filesystem, no
   privilege escalation, seccomp `RuntimeDefault`, no ServiceAccount token.
6. **Digest-pinned image**, so a re-pushed `1.22.0` tag cannot silently change what runs.
7. **Tailnet ACL** restricting `codex-lb:2455` to specific devices — see the Service section;
   requires a tailnet policy edit before it can be enabled.

**Bootstrap token exposure.** With no password set, codex-lb generates a one-time bootstrap token
and prints it to the pod log, where anyone with `kubectl logs` can read it and claim the
dashboard. The token stays valid until a password exists. Setting the password is therefore the
first action in the first port-forward session, not a later step.

**Accepted risk: no NetworkPolicy.** Any pod in the cluster can reach 2455. The controls that
actually matter here are API-key auth on proxy routes and the dashboard password. Worth adding a
policy if network policies are adopted cluster-wide.

Secrets never enter the repo. The repo is public; no token, password, or API key is committed.
The Deployment carries no Secret at all.

## First-run setup

The OAuth flow for adding an account redirects to `localhost:1455/auth/callback`. Driving that
flow from a browser pointed at `codex-lb.tail94c55.ts.net:2455` sends the redirect to the
*browser machine's* localhost, where nothing is listening.

First run therefore happens over a port-forward, which also makes the connection local and so
bypasses the bootstrap token entirely. It targets the **Deployment**, not the Service, because
1455 is deliberately absent from the Service:

```bash
kubectl port-forward -n codex-lb deploy/codex-lb 2455:2455 1455:1455
```

Then, at `http://localhost:2455`:

1. Set the dashboard password.
2. Enable TOTP.
3. Add the ChatGPT account(s) — the OAuth callback now resolves correctly.
4. Enable API key auth and mint one key per client.
5. Set the routing strategy. `Capacity weighted` is the recommended starting point for
   low-volume personal use.

Daily use afterwards goes over the tailnet; the port-forward is not needed again except for
adding further accounts.

## Client wiring

Codex CLI, `~/.codex/config.toml`:

```toml
model = "gpt-5.6-sol"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "openai"
base_url = "http://codex-lb.tail94c55.ts.net:2455/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true
```

Because no reverse proxy sits in the path, WebSocket streaming on
`/backend-api/codex/responses` works without extra upgrade-forwarding configuration.

Existing Codex sessions are filtered by `model_provider` and will not appear under the new
provider until retagged:

```bash
codex-lb codex-sessions retag --from openai --to codex-lb --dry-run
codex-lb codex-sessions retag --from openai --to codex-lb --yes
```

## Verification

Work is complete only when all of the following have been observed:

1. `kubectl get pods -n codex-lb` shows the pod `Running` and `1/1` ready.
2. `kubectl get application codex-lb -n argocd` reports `Synced` and `Healthy`.
3. `kubectl get svc -n codex-lb` shows `ClusterIP` — not LoadBalancer, not NodePort — and
   exposes 2455 only.
   `grep -ri funnel cluster/codex-lb` returns nothing.
4. The Tailscale operator proxy pod exists and `codex-lb` appears in the tailnet device list.
5. From a *second* tailnet device, `GET http://codex-lb.tail94c55.ts.net:2455/v1/models` with a
   valid `sk-clb-...` bearer token returns the model catalog.
6. From outside the tailnet, the node's public address on 2455 refuses the connection.
7. `kubectl get ingress,httproute -A | grep codex` returns nothing.
8. A pod delete followed by a restart preserves accounts and dashboard login, confirming the PVC
   mount is correct.

## Out of scope

- Routing platform-API traffic (embeddings, audio, images) through codex-lb. It cannot do this.
- A unified `/v1` front door across providers. That would need a separate router such as
  LiteLLM, and is a separate spec if wanted later.
- Multi-replica, PostgreSQL, and session-bridge configuration.
- Repointing `cluster/hermes` at codex-lb. Possible later, but not part of this work.
- Prometheus scraping and Grafana dashboards for codex-lb metrics.
