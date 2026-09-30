# CLIProxyAPI on Kubernetes/Tailscale

Pinned release: v8.0.4. One replica, persistent config/provider tokens, private Tailscale service, independent inference and management keys. Codex-LB and Hermes retain their current configuration.

- Inference: `http://cliproxyapi.tail94c55.ts.net/v1`
- Management: `http://cliproxyapi.tail94c55.ts.net/management.html`
- Cluster clients: `http://cliproxyapi.cliproxyapi.svc.cluster.local/v1`
- Initial model: `codex-lb/gpt-6-luna`, with `reasoning.effort: max` on Responses.
- Local credentials: `/Users/tnluser/.config/cliproxyapi/credentials.env` (0600; never commit).

HTTP travels inside the encrypted tailnet. There is no public ingress, Funnel, NodePort, or OAuth callback publication. Management requires the separate admin token. NetworkPolicy permits ingress from Tailscale, Hermes, and this namespace; other cluster clients need an explicit policy entry. Egress remains available to upstream providers.

## Configuration ownership and recovery

Create the `cliproxyapi` namespace and a Secret named `cliproxyapi-bootstrap` containing `config.yaml` and `management-password` before the first Argo sync. `config.example.yaml` is a template, deliberately excluded from Kustomize resources. Replace both placeholders, keeping upstream/client/admin keys distinct.

The init container copies the Secret config only when `/data/config.yaml` is absent. Thereafter the management UI/API edits the writable PVC copy; changing the bootstrap Secret does not overwrite live settings. The management password remains an environment variable, so Secret changes require a restart. Rotate inference/provider keys through the UI/API and update the seed for disaster recovery. Back up the PVC securely: it contains provider refresh tokens and config credentials. PVC pruning is disabled.

The panel downloads on first access and is then cached on the PVC; automatic panel updates are disabled. Update it deliberately when upgrading the backend. Request body logging, native plugins, distributed Home mode, and realtime UDP relay are not enabled.

## Add providers

Use the management UI to add supported provider OAuth accounts or API keys. Browser consent still requires the account owner. Start with one account per provider, inspect `/v1/models`, and test native protocol/tool calling before adding pools. Use device login or local port forwarding when a provider requires a loopback OAuth callback; callback ports are not exposed by the Service. Do not copy Codex-LB refresh tokens: it owns the existing accounts and serves as an API-key upstream here.

Give each provider an explicit model prefix (e.g. `claude`, `gemini`) and avoid disguising a GPT model with a Claude model name. Preserve native protocol paths where possible. Native Claude OAuth may enable cloaking/system-prompt rewriting for non-native clients; evaluate prompt fidelity before using that path. API keys/native Claude Code are preferable when those changes are unwanted.

## Harnesses

OpenCode is the broad multi-provider coding default; configure OpenAI models through its native OpenAI Responses provider, and Claude through its Anthropic provider. Hermes remains the always-on agent. Its current Chat Completions connection works, but a separately tested `codex_responses` provider is preferable for Codex reasoning continuation. No Hermes migration is included here.

Codex CLI custom provider example (`~/.codex/config.toml`):

```toml
model = "codex-lb/gpt-6-luna"
model_provider = "cliproxyapi"
model_reasoning_effort = "max"

[model_providers.cliproxyapi]
name = "CLIProxyAPI"
base_url = "http://cliproxyapi.tail94c55.ts.net/v1"
env_key = "CLIPROXYAPI_API_KEY"
wire_api = "responses"
```

Load the private credentials file into the shell before running the CLI. Codex currently supports Responses as its custom-provider wire protocol. These settings apply to Codex CLI/custom-provider environments, not automatically to managed ChatGPT Work.

Pi's official integration:

```sh
pi install npm:@router-for-me/pi-cliproxyapi-provider
```

Then `/login CLIProxyAPI`; `/settings` → Transport → `websocket-cached`. The deployed bridge uses upstream HTTP/SSE initially; websocket-cached performance is not benchmarked here. See [evaluation](../../docs/cliproxyapi-evaluation.md) for the capability comparison.

## Validation and rollback

Render with `kubectl kustomize cluster/cliproxyapi`. Verify Argo health, `/healthz`, unauthorized inference rejection, management auth, `/v1/models`, and a real Responses request at `max`. Health probes prove the gateway process responds, not upstream quota or generation availability.

Revert the deployment commit to remove the gateway; the protected PVC and manually provisioned Secret remain for recovery. Existing Codex-LB and Hermes require no rollback because they were not changed.
