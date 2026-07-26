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

**Deployment `codex-lb`** — image `ghcr.io/soju06/codex-lb:1.22.0`, pinned by tag. One replica.
Container listens on 2455 (entrypoint pins `--port 2455`) and 1455.

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

**Service `codex-lb`** — `ClusterIP`, ports 2455 and 1455, carrying the Tailscale operator
annotations:

```yaml
annotations:
  tailscale.com/expose: "true"
  tailscale.com/hostname: "codex-lb"
```

This is the same mechanism `cluster/browserless/service.yaml` uses. The operator provisions a
proxy pod joined to the tailnet; no node port and no public IP are involved.

Port 1455 is included because the OAuth callback binds there. Without it, adding an account from
a tailnet browser has no return path.

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
   certificate, no LoadBalancer, no NodePort. `cluster/ingress` is not touched by this work.
2. **Tailnet-only reachability** via the operator annotation.
3. **Dashboard auth** in `standard` mode — password plus TOTP. TOTP is enabled during first-run
   setup, not left for later.
4. **API key auth** enabled for proxy routes. codex-lb rejects non-local proxy requests until
   this is configured, so tailnet clients require it regardless. Each client gets its own
   `sk-clb-...` key so keys can be revoked individually.
5. **Optional tailnet ACL** restricting `codex-lb:2455` to specific devices, as defense in depth
   over the annotation.

Secrets never enter the repo. The repo is public; no token, password, or API key is committed.

## First-run setup

The OAuth flow for adding an account redirects to `localhost:1455/auth/callback`. Driving that
flow from a browser pointed at `codex-lb.tail94c55.ts.net:2455` sends the redirect to the
*browser machine's* localhost, where nothing is listening.

First run therefore happens over a port-forward, which also makes the connection local and so
bypasses the bootstrap token entirely:

```bash
kubectl port-forward -n codex-lb svc/codex-lb 2455:2455 1455:1455
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
3. `kubectl get svc -n codex-lb` shows `ClusterIP` — not LoadBalancer, not NodePort.
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
