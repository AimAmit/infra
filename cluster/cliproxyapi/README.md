# CLIProxyAPI on Kubernetes/Tailscale

CLIProxyAPI v8.0.4 is the active inference gateway. Codex accounts authenticate directly through OAuth; model names have no gateway prefix. Hermes uses this service at `max` reasoning. Codex-LB is retired; its volume is retained for recovery.

- Inference: `http://cliproxyapi.tail94c55.ts.net/v1`
- UI: `http://cliproxyapi.tail94c55.ts.net/management.html`
- Cluster clients: `http://cliproxyapi.cliproxyapi.svc.cluster.local/v1`
- Model: `gpt-6-luna`, with `reasoning.effort: max` on Responses.
- Local client/admin credentials: `/Users/tnluser/.config/cliproxyapi/credentials.env` (0600).

The ClusterIP Service exposes port 80 over Tailscale and forwards to container port 8317. No public ingress, Funnel, NodePort or OAuth callback publication. The separate admin password protects management. NetworkPolicy admits Tailscale, Hermes and the gateway namespace; egress allows provider connections.

## Persistence and recovery

Kubernetes Secret `cliproxyapi-bootstrap` in namespace `cliproxyapi` stores `config.yaml` and `management-password`. The init container seeds `/data/config.yaml` only when absent. Thereafter UI/API edits own the writable PVC copy; changing the Secret does not overwrite it. Password environment changes need a restart. Keep the seed synchronized when changing client keys or major settings.

The `cliproxyapi-data` PVC holds live configuration, OAuth credentials and cached management HTML. Back up it and the Secret securely. OAuth credentials added through the UI are on the PVC, not in the bootstrap Secret. PVC pruning is disabled. Inference/provider keys belong in live config; never git.

The UI password can be recovered with:

```sh
kubectl get secret cliproxyapi-bootstrap -n cliproxyapi -o jsonpath='{.data.management-password}' | base64 -d
```

Hermes reads its inference key from `hermes/hermes-secrets`, key `cliproxyapi-token`. Neither lost local credentials nor a pod restart requires re-onboarding, provided Kubernetes/PVC data survives.

## Providers and protocols

Manage OAuth accounts/API keys in the UI. Start with providers you own and validate `/v1/models`, tools and reasoning before pooling accounts. Names are unprefixed; optional provider namespaces can be introduced only if you deliberately need collision handling. Do not disguise another provider's model with a misleading alias.

Use device login/local port forwarding for loopback callbacks. Callback ports are not exposed. Native Claude OAuth may enable cloaking/system-prompt rewriting for non-native clients; consider native API keys/Claude Code when prompt fidelity matters.

The Codex HTTP/SSE adapter removes `previous_response_id`; replay history including tool results. Responses preserves more native semantics than translating everything to Chat Completions, but is not a transparent pass-through.

## Harnesses and clients

Codex CLI is the preferred coding harness for the current Luna workflow. Claude Code is the preferred Claude harness. Hermes remains the always-on Telegram/MCP agent and keeps its established Chat Completions transport during this endpoint migration. OpenCode and Pi remain alternatives for multi-provider switching/extensions.

```toml
model = "gpt-6-luna"
model_provider = "cliproxyapi"
model_reasoning_effort = "max"

[model_providers.cliproxyapi]
name = "CLIProxyAPI"
base_url = "http://cliproxyapi.tail94c55.ts.net/v1"
env_key = "CLIPROXYAPI_API_KEY"
wire_api = "responses"
```

OpenAI SDK/curl clients use the same `/v1` base URL and client key. Codex custom-provider settings apply to CLI/custom-provider environments, not automatically managed ChatGPT Work.

## Validation and rollback

Render with `kubectl kustomize cluster/cliproxyapi`. Source the private credentials file and run `python3 cluster/cliproxyapi/smoke.py`: it checks auth separation, bare Luna model/no retired prefix, SSE tools at max and a nonstreaming history-replay continuation. This consumes a small amount of quota.

Health probes prove the process responds, not upstream generation availability. Backend is digest-pinned; panel periodic updates, request body logging, native plugins, Home and realtime UDP relay are disabled. Model catalogs are still refreshed upstream.

A private pre-migration config backup exists at `~/.config/cliproxyapi/config-before-direct-oauth.yaml`. Recover an older deployment from git if needed; retained Codex-LB data is documented in `cluster/codex-lb/README.md`. See [evaluation](../../docs/cliproxyapi-evaluation.md) for capabilities and historical comparison.
