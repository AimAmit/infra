# CLIProxyAPI evaluation — 2026-09-30

## Current deployment — direct OAuth

CLIProxyAPI is now the active gateway, backed directly by user-added Codex OAuth accounts. Use the bare model `gpt-6-luna` at `max`; there is no Codex-LB backend or prefix. Hermes uses the in-cluster CLIProxyAPI endpoint and its own Secret key. Codex-LB is retired after validating the new route; only its protected PVC remains managed for recovery.

For this user's workflow, prefer Codex CLI with Luna and Claude Code with Claude. Multi-provider UI switching is the reason to consider OpenCode/Pi, not a reason to replace a preferred native harness.

The original research and initial deployment below describe the transition's starting point. Suggestions to retain Codex-LB or use prefixed models are historical and superseded by the direct OAuth migration.

## Original decision (superseded)

Deploy CLIProxyAPI v8.0.4 alongside Codex-LB v1.24.0. CLIProxyAPI is a strong multi-provider/protocol gateway; Codex-LB remains valuable as the Codex account/quota manager. Initially route `codex-lb/gpt-6-luna` through Codex-LB, then onboard additional providers directly. This avoids duplicate refresh-token ownership and leaves existing Hermes traffic untouched.

This assessment inspected the release source/config, project integration docs, deployed infrastructure, and Codex-LB's matching release. Harness recommendations reflect architectural fit, not comparative performance benchmarks. Provider functionality requires valid credentials and model/account entitlement; only the initial Codex-LB path is available for live validation.

## Capability map

| Area | CLIProxyAPI v8.0.4 capability | Practical limit/use |
|---|---|---|
| Native provider login | Codex/ChatGPT, Claude Code, Gemini CLI/AI Studio, Antigravity, Kimi.com, Kimi.ai, xAI/Grok, Devin, Meta; Vertex credential import | These are implemented login/adapters, not a promise of equal provider maturity or subscription portability. Confirm account permissions and test each selected provider. |
| API-key upstreams | Gemini, Interactions, Vertex, Codex, Claude, xAI, Meta, OpenAI-compatible services | OpenRouter/DeepSeek/GLM and similar APIs can be configured when compatible; they are not all native subscription integrations. |
| Client protocols | OpenAI Chat Completions/Completions, Responses HTTP/WebSocket/compact, Anthropic Messages/count_tokens, Gemini generateContent/streamGenerateContent/Interactions, Codex route aliases | Prefer native protocol per provider; translation can lose provider-specific semantics. |
| Agent essentials | Tool/function calling, streaming, image inputs, reasoning/thinking translation | Model/backend dependent; tools run in the harness, not magically in the gateway. |
| Images/video/live | Image generation/edit routes; xAI and OpenAI-shaped video routes; Codex realtime/live and optional WebRTC relay | Not full OpenAI API parity. No general embeddings or ordinary speech/transcription routes found; keep separate services for these. UDP relay needs separate networking and is not enabled here. |
| Pooling | Round-robin, weighted-round-robin, fill-first, priorities, per-account weights | Useful across provider accounts, but not the same as Codex-LB's quota-aware selection. |
| Session locality | Soft session affinity, TTL, child-session inheritance | Enabled here. Failover may move accounts; do not assume server-side continuation IDs are portable. |
| Resilience | Retries, cooldowns, quota/model failover, credential error classification, pre-first-byte streaming retries | Keep retries small when chaining gateways; midstream replay is not generally safe. |
| Model controls | Aliases, prefix namespaces, pools, exclusions, payload overrides, reasoning translation | Explicitly declare `max` for Luna. Generic compatible model defaults can otherwise clamp unsupported levels. Avoid deceptive cross-provider aliases. |
| Operations | Config/auth hot reload, refresh tokens, UI, TUI, management APIs, OAuth/auth-file management and quota introspection | Writable persistent config/auth storage needed; management needs separate credentials. |
| Persistence | Local auth files; optional Git, PostgreSQL, S3 storage | Single PVC suffices here. Storage options do not mean safe active-active refresh ownership without further validation. |
| Analytics | External usage consumers/usage queue, compatibility flags | Built-in usage stats removed in v6.10.0. Queue reads consume short-lived data; no built-in durable cost history promised. Use CPA Usage Keeper or CPA-Manager-Plus if needed. |
| Extensions | Model/auth/frontend-auth/executor/routing/scheduler/translator/interceptor/thinking/usage/management/CLI plugins | Native plugins execute trusted in-process code; disabled here. |
| Distributed operation | Optional Home control plane, mTLS policy/concurrency | Unnecessary for this single-node deployment; not tested. |

## Comparison with current Codex-LB

| Concern | Codex-LB v1.24.0 | CLIProxyAPI v8.0.4 |
|---|---|---|
| Best fit | Codex accounts, quota management, reporting | Many provider accounts and client protocols |
| Provider breadth | Codex-centered, plus custom OpenAI-compatible model sources | Multiple native OAuth adapters plus API-key integrations |
| Routing | Capacity/relative availability/usage weighting, round-robin, fill-first, sequential/reset drain, single-account | Round-robin, weighted-round-robin, fill-first, priority, cooldown/failover |
| Continuation | Hard ownership for previous response/uploaded file IDs, plus sticky policies | Universal soft affinity and failover; Codex HTTP/SSE strips previous_response_id, so replay full history |
| Analytics | Built-in quota/usage/cost/reasoning dashboard and API-key policies | External durable usage system needed |
| API coverage | Responses/WS/compact, images/files/audio transcription/realtime in its documented surface | Broader protocol translation; different media endpoints, not a superset of every OpenAI route |

Therefore “better” depends on the job. CLIProxyAPI should be the multi-provider front door; retain Codex-LB for its quota/continuation/analytics strengths. Chaining adds another failure point and some overhead, so route additional providers directly through CLIProxyAPI rather than inserting unnecessary proxies. Codex-LB custom-source reporting also has limitations: its documented reasoning-report path does not cover arbitrary OpenAI-compatible sources.

The initial native Codex bridge preserves the Responses protocol and tested `max` reasoning, but is not transparent: its HTTP/SSE executor deletes `previous_response_id` and normalizes other request fields. Use client-managed history replay and test compaction/reasoning replay on your workload. Keep direct Codex-LB access for applications requiring server-side continuation IDs or other routes the gateway does not implement.

## Best harness pairing

1. **OpenCode:** practical first choice for multi-provider coding and model switching. Use the native OpenAI Responses adapter for GPT/Codex and native Anthropic for Claude. A universal OpenAI-compatible Chat Completions adapter is less suitable for encrypted reasoning and continuation state.
2. **Hermes:** best continuation of the existing always-on Telegram/MCP/personal-agent setup. The new gateway expands available models without replacing the agent. Keep current routing initially; later test Hermes `codex_responses` mode before moving long reasoning sessions.
3. **Pi:** best candidate if you want a lean, programmable coding harness with a maintained CLIProxyAPI-specific provider extension. Official docs provide `npm:@router-for-me/pi-cliproxyapi-provider`, login integration and `websocket-cached` transport. Worth a real workload comparison with OpenCode; no measured winner claimed.
4. **Codex CLI:** best fit for Codex/OpenAI-native coding fidelity; configure a custom Responses provider. It is not an equally native general-purpose Claude/Gemini harness. Managed ChatGPT Work does not inherit arbitrary CLI inference settings.
5. **Claude Code:** best fit when Claude is the primary model and native Anthropic tool/prompt behavior matters.

## Use it fully without unnecessary infrastructure

- Establish the prefixed Luna route and verify `max`, streaming and tools. Keep Hermes unchanged until gateway behavior is proven.
- Add the providers you actually own: one account each initially. Validate native protocol, tool schema, image support, reasoning and refresh behavior before pooling accounts.
- Use distinct prefixes, explicit thinking levels, session affinity and per-client keys. Keep response state with its owning account. Configure retry limits once rather than multiplying them across every layer.
- Add weighted routing only after observing quota/latency differences. Use exclusions to prevent clients selecting unavailable models.
- Keep Codex-LB's dashboard for existing Codex usage. Add one persistent CLIProxyAPI usage collector only when you need cross-provider cost/history; verify which forwarded requests its accounting actually covers.
- Compare OpenCode and Pi on the same real repository task: correctness, tool reliability, continuation/compaction, time to first token, total latency and quota consumption. Select based on observed workflow fit.
- Back up provider tokens/config, keep management private, and deliberately upgrade pinned backend/panel versions. Do not enable plugins, Home, extra databases or realtime UDP until a concrete workload needs them.

## Deployment evidence

Deployed on 2026-09-30 via ArgoCD: application Synced/Healthy, one ready gateway pod, bound 1Gi PVC, and online Tailscale peer `cliproxyapi` (`100.114.23.38`). Backend image reports v8.0.4/d33f63f. No existing Codex-LB or Hermes resource was changed.

Verified from the local machine across Tailscale:

- `/healthz`, management HTML, and authenticated v8 management configuration return success.
- Missing/invalid inference keys return 401; the inference key is rejected by management while the separate admin key succeeds.
- `/v1/models` includes `codex-lb/gpt-6-luna`.
- A real SSE Responses request produced 11 events, completed with `reasoning.effort=max`, and returned a schema-valid forced function call.
- A second nonstreaming request replayed history and the function result, then completed with the requested `DONE` answer and `max` reasoning.
- Writable config has mode 0600 and is owned by the nonroot runtime UID. Management panel is cached on persistent storage.

The runnable check is `cluster/cliproxyapi/smoke.py`. Other providers, WebSocket transport, image/video/live APIs, cross-account failover, and harness workload comparisons were inspected/documented but not live-tested without credentials. Image pinning does not pin the upstream periodically refreshed model catalogs; review catalog changes if reproducibility matters.

## Sources

- [CLIProxyAPI repository/readme](https://github.com/router-for-me/CLIProxyAPI/tree/v8.0.4), [v8.0.4 release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.4)
- [Released configuration and provider options](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.4/config.example.yaml)
- [Login flags](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.4/cmd/server/main.go), [API routes](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.4/internal/api/server_routes.go)
- [Quick start](https://help.router-for.me/introduction/quick-start.html), [OpenCode integration](https://help.router-for.me/agent-client/opencode.html), [Pi integration](https://help.router-for.me/agent-client/pi.html), [Codex integration](https://help.router-for.me/agent-client/codex.html)
- [Usage queue implementation](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.4/internal/api/handlers/management/usage.go)
- [Codex-LB v1.24 client setup](https://github.com/soju06/codex-lb/blob/v1.24.0/docs/client-setup.md), [usage reporting](https://github.com/soju06/codex-lb/blob/v1.24.0/docs/usage-reporting.md)
- [Official Codex custom-provider settings](https://developers.openai.com/codex/config-reference/)
