# Codex-LB — retired

CLIProxyAPI now authenticates Codex accounts directly through OAuth. Hermes and API clients use the bare `gpt-6-luna` model on CLIProxyAPI. Codex-LB has no running deployment or service/Tailscale exposure.

The Argo application is retained solely to manage the protected `codex-lb-data` PVC. Its account database and encryption key have not been deleted. Remove this archive only after explicitly deciding its data is no longer needed.

If recovery is needed, restore this directory's deployment and service from commit `8c45f99`, then separately restore client routing/credentials. The private gateway pre-migration config backup is `~/.config/cliproxyapi/config-before-direct-oauth.yaml`. Retired manifests are in git history, not active Kustomize resources.
