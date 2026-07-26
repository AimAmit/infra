# Agent Operations Vault: Obsidian second brain for hermes, tiered access

Date: 2026-07-26
Status: pending review

## Summary

Give hermes a persistent, structured knowledge base backed by the Obsidian vault at
`/Users/tnluser/obsidian/obsidian-vault`, with an always-on copy in the cluster, under a strict
three-tier access model:

- **rw/** — hermes reads and creates notes
- **ro/** — hermes reads only, enforced by one-way sync
- **private/** — hermes has no access; the content never leaves the Mac

This merges two designs: the *access-control architecture* from our brainstorm (tiered sync,
restricted MCP service, create-only writes) and the *content conventions* from the community
"Obsidian + AI assistant" pattern (three-tier memory lifecycle, daily notes, content routing,
persona files).

The two "three-tier" ideas are different axes and both apply:

| Axis | Tiers | Enforced by |
| --- | --- | --- |
| Access (ours) | rw / ro / private | sync topology + MCP service |
| Lifecycle (theirs) | hot memory / living files / daily notes | hermes conventions + prompts |

## Architecture

```
Mac: /Users/tnluser/obsidian/obsidian-vault/
├── rw/         Syncthing "vault-rw"  Send & Receive  ⇅
├── ro/         Syncthing "vault-ro"  Send Only       ⇩ (Mac → cluster only)
├── private/    no share — never leaves the Mac
└── .obsidian/  not shared

        ⇅ Syncthing over tailnet only (no global discovery, no relays)

Cluster: namespace agent-knowledge
├── PVC agent-vault-data (RWO, local-path, Prune=false)
│   ├── rw/     Send & Receive  + cluster-side git history (in .stignore)
│   └── ro/     Receive Only
├── Deployment syncthing   (mounts PVC)
├── Deployment vault-mcp   (mounts PVC; rw/ writable, ro/ read-only mount)
│   └── Service vault-mcp  ClusterIP :8080  — no tailscale.com/expose, no ingress
└── hermes → http://vault-mcp.agent-knowledge.svc.cluster.local:8080/mcp
             Bearer token from hermes-secrets
```

One Obsidian vault on the Mac; Obsidian's own search, graph view, and backlinks span all three
folders locally. Only `rw/` and `ro/` have cluster copies.

### Why the tier boundary lives in the sync layer

An MCP allowlist is a code path; code paths have bugs (traversal, symlinks, refactors). Absence
does not. Because `private/` is never shared, no MCP bug, compromised pod, or NetworkPolicy
mistake can disclose it. The MCP path rules below are the *second* line of defense, not the
first.

`ro/` gets the same treatment in the write direction: the Mac share is **Send Only** and the
cluster share **Receive Only**, so even if the MCP service is tricked into writing inside `ro/`,
Syncthing never propagates it to the Mac and reverts the cluster copy on next scan.

### Why Syncthing and not git as transport

Git was considered (audit, revert). Syncthing wins because per-folder shares with directional
types implement the access tiers *in the transport itself*, and because Obsidian's
rewrite-on-focus behavior fights a Mac-side git plugin. History is still git — but cluster-side
only: a sidecar commits `rw/` every few minutes into a repo that sits in Syncthing's `.stignore`,
so the Mac never sees `.git`. Every agent write becomes a reviewable, revertible commit without
any Mac-side tooling.

### Syncthing hardening (both sides)

- `STGLOBALDISCOVERY=false`, relays disabled, NAT traversal off. Devices address each other by
  tailnet name only: the pod reaches the Mac via the existing subnet-router/egress pattern
  (`cluster/hermes/tailscale-service.yaml` precedent); the Mac reaches the pod via a
  tailnet-exposed Service like codex-lb's.
- GUI (8384) gets credentials on both sides, and the cluster GUI port is **not** on any Service
  — `kubectl port-forward` only. An open Syncthing GUI allows reconfiguring shares, which is
  arbitrary read/write of the PVC. Same reasoning as leaving 1455 off the codex-lb Service.
- Folder IDs: `vault-rw`, `vault-ro`. `.stignore` in `rw/`: `.git`.

## Vault layout (content conventions re-cut by access tier)

Folders below are the community pattern's structure, redistributed so that what the agent may
*do* to a note is decided by where it lives:

```
obsidian-vault/
├── rw/
│   ├── Daily/                    # YYYY-MM-DD.md — append via new agent-log notes
│   ├── Inbox/Agent Captures/     # anything hermes saves on request
│   ├── Proposals/                # agent-suggested edits to ro/ notes, as new files
│   └── System/logs/              # issues-fixes log entries, one file per entry
├── ro/
│   ├── System/Assistant/
│   │   ├── context.md            # operations, health-adjacent overview (curated)
│   │   ├── preferences.md        # communication style, delivery rules
│   │   └── environment.md        # hardware, services, known issues
│   ├── Work/
│   └── People/                   # only people the agent should know about
└── private/
    ├── Personal/Health/
    ├── Personal/Finance/
    ├── Journal/
    └── People-private/
```

Placement decisions that differ from the community template, deliberately:

- **Persona files are `ro/`, not agent-writable.** hermes reads them every session; letting it
  write its own standing instructions is a self-persistence channel for prompt injection. When
  hermes wants a change, it writes to `rw/Proposals/` and the user merges by hand in Obsidian.
- **`People/`, `Personal/Health/`, `Personal/Finance/` default private.** The community template
  gives the agent all of this; that is exactly the content the private tier exists for. A
  curated subset of People can be promoted to `ro/People/` explicitly.
- **Daily notes are agent-*readable* but the agent never edits a daily note in place.** It
  appends by creating `Daily/YYYY-MM-DD--agent-NNN.md` fragments; the user's own daily note
  stays theirs. (Create-only rule, below.)

### Cross-tier link policy (decided, not discovered)

A `[[private/...]]` link inside an `rw/` or `ro/` note leaks the private note's *filename* into
the agent's context even though the file never syncs. Policy: **cross-tier links into `private/`
are allowed but must use an alias** (`[[private/xyz|personal note]]`) when the filename itself
is sensitive. This is a convention for the user, not enforceable; documented so the leak is a
choice rather than a surprise.

## vault-mcp service

Small HTTP MCP server (FastMCP or equivalent), image built in this repo, mounted:

- `/vault/rw` from the PVC subPath `rw` — writable
- `/vault/ro` from the PVC subPath `ro` — `readOnly: true` volumeMount (kernel-enforced,
  third line of defense behind sync direction and app checks)

### Tools

| Tool | Access | Notes |
| --- | --- | --- |
| `vault_search(query, tier?)` | rw+ro | ripgrep-backed full-text; returns path + snippet |
| `vault_read(path)` | rw+ro | whole note |
| `vault_backlinks(path)` | rw+ro | notes linking to this one |
| `vault_neighbors(path)` | rw+ro | outgoing `[[links]]`, tags, frontmatter |
| `vault_capture(title, content, tags)` | create in `rw/Inbox/Agent Captures/` | |
| `vault_log_daily(content)` | create `rw/Daily/YYYY-MM-DD--agent-NNN.md` | server picks NNN |
| `vault_propose(target_path, rationale, content)` | create in `rw/Proposals/` | never touches target |
| `vault_status()` | none | sync health, last git commit, counts |

No delete tool. No rename tool. No generic write tool. A capability that does not exist cannot
be talked into firing.

The "knowledge graph" is `vault_backlinks` + `vault_neighbors` over parsed markdown links, tags,
and frontmatter — Obsidian's own model. In-memory index rebuilt on file change (watchdog) or
lazily per request; no database.

### Containment rules (the actual security core)

Every path argument, before any filesystem call:

1. Reject absolute paths, `..` segments, null bytes, backslashes.
2. Join to the tier root, `realpath()` the result, assert the resolved path is still under
   `/vault/rw` or `/vault/ro`. This kills symlink escapes — vaults do contain symlinks.
3. Reject dotfiles and dot-directories (`.obsidian`, `.git`, `.stignore`, `.DS_Store`).
4. Only `.md` files are readable or creatable.
5. **All writes are `O_CREAT|O_EXCL`** — create-only, atomic, can never modify or truncate an
   existing note. Overwrite-prevention is a syscall flag, not a convention. This also kills the
   Obsidian/Syncthing conflict problem: agent and user never edit the same file, so
   `*.sync-conflict-*` duplicates cannot arise from agent activity.
6. Write size cap (64 KiB/note), rate cap (30 creates/hour) — a runaway loop cannot flood the
   vault or the sync channel.
7. Bearer token required on every request; token lives in `hermes-secrets` under
   `vault-mcp-token`, minted out-of-band, never in git.

### Prompt-injection stance

Note content is untrusted input: anything that reaches the vault (email-derived text, web clips,
Telegram pastes, headlines) can carry instructions. Mitigations, in order of value:

- Persona files are read-only to the agent (no self-persistence).
- Tool results wrap note bodies in explicit delimiters marking them as quoted document content.
- The create-only write set bounds worst-case impact: an injected instruction can add junk notes
  to `rw/`, never alter existing knowledge, never touch `ro/` or `private/`.
- Rate cap bounds the junk.

Residual risk accepted: injected content can still bias what hermes *says* and what it writes
into new captures. That is inherent to letting an agent read ingested text.

## Lifecycle conventions (the community pattern, adopted)

These are hermes-side conventions (system prompt + `MEMORY.md`), not infrastructure:

- **Tier 1 hot memory** — hermes's built-in `MEMORY.md`/`USER.md`, kept ≤ ~4–6 K chars. Every
  char is paid per turn against pooled ChatGPT quota shared with Codex CLI, so lean matters.
- **Tier 2 living files** — when hot memory nears capacity, stable entries are *proposed* for
  promotion into `ro/System/Assistant/` via `vault_propose`; user merges.
- **Tier 3 daily notes** — searchable timeline via `vault_log_daily` fragments.
- **Content routing** — operational events → daily log; system fixes → `rw/System/logs/`;
  corrections → hot memory; recurring workflows → hermes skills.
- **Scheduled briefings** — `hermes cron` in-cluster (morning briefing, etc.), writing via the
  same MCP tools. Cron jobs live in hermes, not Mac crontab, because always-on was the point.

## Kubernetes packaging

Follows the repo pattern exactly (kustomize, ArgoCD app-of-apps, `CreateNamespace=true`,
selfHeal + prune):

```
bootstrap/apps/agent-knowledge.yaml
cluster/agent-knowledge/
  argocd-application.yaml
  kustomization.yaml
  pvc.yaml            # 5Gi, Prune=false annotation (holds the only always-on copy + git history)
  syncthing.yaml      # Deployment + config; GUI not on any Service
  syncthing-service.yaml  # sync port only, tailscale.com/expose for Mac→pod dial
  vault-mcp.yaml      # Deployment: image from this repo; ro/ mounted readOnly
  vault-mcp-service.yaml  # ClusterIP :8080, cluster-internal only
```

Cluster facts already verified: single node, `local-path` default SC, **no volume expansion**
(5Gi is a one-shot decision; vault is text, 5Gi is years), reclaim `Delete` (hence Prune=false,
same lesson as codex-lb), Tailscale operator + subnet router present, hermes MCP support
confirmed (`hermes mcp add --url ... --auth header`).

Secrets: `vault-mcp-token` (hermes-secrets + vault-mcp env), Syncthing GUI passwords — all
created out-of-band with kubectl; repo is public, nothing sensitive committed.

## Rollout order

1. **Mac-local first, zero infra**: create the `rw/ro/private` folder split in the vault; point
   hermes at the six operations via its existing SSH path (thin wrapper script with the same
   containment rules). Proves the interaction loop is worth having before any cluster work.
2. Vault-mcp image + manifests; deploy with PVC seeded by one-shot copy.
3. Syncthing both sides, pair over tailnet, verify tier semantics (below).
4. Switch hermes MCP from SSH wrapper to cluster URL; move briefings to `hermes cron`.

## Verification

1. Note created in Obsidian `rw/` appears in pod `/vault/rw`; and vice versa via
   `vault_capture` — appears in Obsidian.
2. Note created in Obsidian `ro/` appears in pod; file touched inside pod `ro/` does **not**
   appear on Mac and is reverted on next Syncthing scan.
3. Nothing under `private/` exists anywhere in the cluster (`find /vault -path '*private*'`
   empty; Syncthing share list shows exactly vault-rw, vault-ro).
4. `vault_read("../../etc/passwd")`, `vault_read("/vault/rw/../ro/x")`, symlink inside `rw/`
   pointing at `private/` — all rejected; symlink read attempt fails the realpath check.
5. `vault_capture` twice with same title → second gets a distinct filename (O_EXCL respected,
   no overwrite).
6. Unauthenticated request to vault-mcp → 401. `kubectl get svc -n agent-knowledge` shows no
   tailscale annotation on vault-mcp, no ingress/httproute anywhere in the namespace, Syncthing
   Service carries sync port only.
7. Syncthing cluster pod: global discovery and relays disabled in its config; device list
   contains exactly the Mac.
8. Pod restart: vault intact, git history intact (PVC), hermes `vault_status()` healthy.
9. Mac asleep: hermes still reads/writes cluster copy; on wake, Syncthing reconciles; agent
   writes appear in Obsidian.
10. Cluster-side `git log` in `rw/` shows one commit per agent write window; `.git` absent on
    the Mac.

## Out of scope

- Embeddings / semantic search (ripgrep + links first; add later behind the same MCP surface).
- NetworkPolicy (same accepted-risk posture as codex-lb; revisit cluster-wide).
- Todoist/Calendar/Finance ingestion pipelines from the community post — separate spec per
  integration; each is a new untrusted-input channel and gets its own review.
- Multi-device Syncthing (phone, second laptop).
- Backups beyond the cluster git history + PVC (worth a follow-up: nightly bundle push).
