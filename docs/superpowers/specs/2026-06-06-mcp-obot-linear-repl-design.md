# Obot Gateway + Linear MCP + OpenHands REPL — Design

**Date:** 2026-06-06
**Status:** Draft, pending implementation
**Scope:** Add a single self-contained OpenHands SDK sample that consumes a remote MCP (Linear) through the Obot gateway. Evaluation harness for the configure-connectors design landing in Studio (`~/src/studio`); the sample exercises the integration boundary Studio will own.

## Goal

Stand up Obot locally, federate Linear through it (OAuth already mediated by Obot's UI), and drive a Python REPL that uses OpenHands' SDK to call Linear tools through Obot. The sample is the cheapest way to surface the load-bearing integration questions for the Studio production design: MCP-client authentication shape, gateway dispatcher vs per-tool advertisement, OAuth recovery UX, and the REST surface Studio's backend will call.

## Non-goals

- Multi-user OAuth in code. Obot supports it natively (per-user OAuth tokens, IdP integration, OAuth diagnostics) but the sample exercises a single user (the developer). Multi-user is the *next* sample on the roadmap (two parallel intents, two MCPs, one gateway).
- Per-intent allow-list filtering. Studio concern; not exercised here.
- Driving OAuth from the sample. Obot's admin UI does this correctly; reimplementing it adds no information.
- Building our own dispatcher. We see what Obot already exposes through its `streamable-http` surface and judge whether per-tool advertisement is acceptable for Studio's system-message footprint or whether a dispatcher layer is still needed.
- Productionization (TLS termination, persistence, multi-tenant scaling). Single-host local eval only.
- Comparing gateways head-to-head in code. The vendor evaluation lives in the brainstorm; this sample commits to Obot.
- OTel trace propagation across the OpenHands → Obot → remote-MCP boundary in the sample's code. Studio production wires this end-to-end per lock-in #9; the sample omits it to keep the evaluation focused on MCP plumbing and OAuth recovery. Obot's OTel hooks exist and work — proving them in code is a separate exercise, not what this sample is testing.

## Key external facts (verified)

- **Obot exposes everything via `streamable-http` transport, regardless of the underlying server runtime.** (Quoted from Obot's `concepts/mcp-gateway.md`.) Single transport surface, even for stdio MCP servers that Obot itself hosts.
- **OpenHands' MCP client (fastmcp) supports `streamable-http` natively.** `fastmcp.mcp_config.RemoteMCPServer.transport` accepts `Literal["http", "streamable-http", "sse"]`.
- **Obot ships first-class OTel** (exercised by Studio production per lock-in #9; the sample does not exercise it). `pkg/services/otel.go` uses `go.opentelemetry.io/contrib/exporters/autoexport`, configured via standard `OTEL_*` env vars; W3C TraceContext + Baggage propagators are set globally; traces, metrics, and logs are all emitted. Studio's existing OTel collector receives them and links them to Studio's own service spans; the sample skips the wiring to stay focused on the MCP-plumbing evaluation.
- **Obot's gateway authenticates each user and proxies with that identity.** Per-user OAuth tokens; admins can deploy one shared multi-user MCP and have each user supply per-user header values that pass through to the upstream MCP.

## Architecture

A single file: `samples/mcp_obot_linear_repl.py`. Runnable directly via `uv run python samples/mcp_obot_linear_repl.py`.

```
┌──────────────────────────────────────────┐
│ samples/mcp_obot_linear_repl.py          │  uv run, REPL loop
│  • OpenHands LLM (Minimax via OpenRouter)│
│  • Agent + Conversation                  │
│  • MCPConfig → Obot (streamable-http)    │
└──────────────────┬───────────────────────┘
                   │  streamable-http
                   │  (Authorization: Bearer <OBOT_API_KEY>)
                   ▼
┌──────────────────────────────────────────┐
│ Obot (docker run, port 8080)             │  external, set up out-of-band
│  • Linear federated via admin UI         │
│  • Per-user OAuth tokens, API-key auth   │
└──────────────────┬───────────────────────┘
                   │
                   ▼
            linear.app remote MCP
```

### Components

1. **LLM + Agent + Conversation setup.** Same pattern as `samples/recap.py` and `samples/critic_refinement.py`: load `.env`, build an `LLM` for Minimax via OpenRouter, construct `Conversation(agent=agent, workspace=tempfile.mkdtemp(...))`, `NeverConfirm()` policy, no security analyzer.

2. **`OBOT_URL` / `OBOT_API_KEY` constants.** Read from environment. Default `OBOT_URL=http://localhost:8080`. `OBOT_API_KEY` has no default — failure to set it raises a clear error.

3. **`mcp_config`.** Built via `MCPConfig.model_validate({...})`:
   ```python
   MCPConfig.model_validate({
       "mcpServers": {
           "obot": {
               "transport": "streamable-http",
               "url": f"{OBOT_URL}/mcp",      # exact path TBD on first run
               "headers": {"Authorization": f"Bearer {OBOT_API_KEY}"},
           }
       }
   })
   ```
   Passed to `Agent(llm=llm, mcp_config=mcp_config)`. OpenHands surfaces Obot-mediated tools as `mcp__obot__<linear-tool-name>` (e.g. `mcp__obot__linear_create_issue`).

4. **`obot_status()` helper.** `GET {OBOT_URL}/api/<TBD-on-first-run>` with the Bearer token, prints the user's connected MCPs and the catalog of available servers. Implements the `/status` slash command.

5. **REPL loop.** Identical shape to `samples/recap.py`:
   - Clean `^C` exit via SIGINT handler raising `KeyboardInterrupt`.
   - `> ` prompt.
   - `/quit` / `/exit` / EOF / `KeyboardInterrupt` → exit.
   - `/status` → `obot_status()`.
   - `/connect <connector>` → print the reconnect URL for the named connector (e.g. `http://localhost:8080/user-settings/connectors/linear`). No in-band OAuth driving; the URL points the user at Obot's existing user-settings flow. Used both proactively and as the diagnostic the sample prints automatically when it detects an OAuth-class tool failure (see [Error handling](#error-handling)).
   - Empty line → continue.
   - Anything else → `conversation.send_message(line)` + `conversation.run()`.

6. **`print_oauth_banner_on_failure(event)` callback.** Registered as a `Conversation` callback. Inspects every `ObservationEvent`; when the observation text matches OAuth-class failure patterns (`401`, `Unauthorized`, `OAuth`, `token expired`, `consent required`, `invalid_token`), prints a prominent banner above the agent's normal output:
   ```
   ────────────────────────────────────────────────────────
   ⚠  Linear OAuth needs attention for this user.
   Reconnect at: http://localhost:8080/user-settings/connectors/linear
   After reconnecting, type your request again in this REPL.
   ────────────────────────────────────────────────────────
   ```
   The agent itself also sees the failure in its observation history and can surface it in its own response, but the banner guarantees the user sees the specific URL regardless of how the agent phrases its message.

### File layout impact

- New file: `samples/mcp_obot_linear_repl.py`.
- `.env.example` adds: `OBOT_URL=http://localhost:8080`, `OBOT_API_KEY=`.
- `pyproject.toml`: no changes; `openhands-sdk`, `openhands-tools`, `python-dotenv` already present, and the sample adds no new deps now that OTel is deferred to Studio production (lock-in #9). When Studio wires OTel, it does so in Studio's own service, not in this sample.

## Out-of-band setup the user does once before running

The sample uses **Obot's embedded Postgres** (bundled inside the Obot image, pgvector preinstalled). No standalone PG container, no modification to Studio's compose. This is the minimum-friction deployment for the experiment; the production deployment shape — swapping Studio's PG image and sharing the cluster — is captured under [Design lock-ins for Studio production](#design-lock-ins-for-studio-production) as the target Studio will move to once this approach is validated.

1. **Generate the encryption-config YAML for Obot** (lives in `${DATA_DIR}/keys/`, mounted into the Obot container; see lock-in #8 for the centralized key-management pattern):
   ```bash
   mkdir -p ${HOME}/.obot/data/keys
   ENC_KEY=$(openssl rand -base64 32)
   cat > ${HOME}/.obot/data/keys/obot-encryption.yaml <<EOF
   apiVersion: apiserver.config.k8s.io/v1
   kind: EncryptionConfiguration
   resources:
     - resources:
         - credentials.obot.obot.ai
         - users.obot.obot.ai
         - identities.obot.obot.ai
         - mcpoauthtokens.obot.obot.ai
         - mcpoauthpendingstates.obot.obot.ai
         - mcpauditlogs.obot.obot.ai
         - policyviolations.obot.obot.ai
         - properties.obot.obot.ai
       providers:
         - aescbc:
             keys:
               - name: vibedata-obot-key-1
                 secret: ${ENC_KEY}
         - identity: {}
   EOF
   chmod 600 ${HOME}/.obot/data/keys/obot-encryption.yaml
   ```
   Keep this file stable across `docker run` invocations — if the key changes, Obot can't decrypt previously stored OAuth refresh tokens. Rotation is the standard k8s `EncryptionConfiguration` two-key dance: prepend a new key as the primary, restart, run Obot's storage-rewrite job, then remove the old key.

2. **Run Obot** with the production-target configuration applied to the experiment:
   ```bash
   docker run -d --name obot -p 8080:8080 \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v ${HOME}/.obot/data:/data \
     -e OBOT_SERVER_ENABLE_AUTHENTICATION=true \
     -e OBOT_ENABLE_AGENTS=false \
     -e OBOT_BOOTSTRAP_TOKEN=<bootstrap-token> \
     -e OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true \
     -e OBOT_SERVER_HOSTNAME=http://localhost:8080 \
     -e OBOT_SERVER_ENCRYPTION_PROVIDER=custom \
     -e OBOT_SERVER_ENCRYPTION_CONFIG_FILE=/data/keys/obot-encryption.yaml \
     -e OBOT_SERVER_MCPRUNTIME_BACKEND=docker \
     -e OBOT_SERVER_ENABLE_REGISTRY_AUTH=true \
     -e OBOT_SERVER_AUDIT_LOGS_MODE=disk \
     -e OBOT_SERVER_AUDIT_LOGS_COMPRESS_FILE=false \
     -e OBOT_SERVER_MCPOAUTH_CLIENT_EXPIRATION=90d \
     -e OBOT_SERVER_DISABLE_UPDATE_CHECK=true \
     ghcr.io/obot-platform/obot:latest
   ```
   Notes (every env var here is locked-in per lock-in #7 except deployment-target deltas listed in that lock-in):
   - **No `OBOT_SERVER_DSN`** — Obot uses its embedded Postgres (pgvector preinstalled). Production swaps to `OBOT_SERVER_DSN=postgres://...studio-postgres:5432/obot...` per lock-ins #1–4.
   - **`-v ${HOME}/.obot/data:/data`** persists the embedded PG data dir across container restarts (critical — the encryption key only works against the data it was used to encrypt).
   - **`OBOT_SERVER_HOSTNAME=http://localhost:8080`** — must match the browser-reachable URL for OAuth redirects. Production overrides to the customer's user-browser-reachable URL.
   - **`OBOT_SERVER_ENCRYPTION_PROVIDER=custom` + `OBOT_SERVER_ENCRYPTION_CONFIG_FILE=/data/keys/obot-encryption.yaml`** — encrypts OAuth tokens, DCR client secrets, and other sensitive resources at rest in Postgres. The file approach (vs the inline `OBOT_SERVER_ENCRYPTION_KEY` env var) keeps the key out of process env dumps, supports k8s-style two-key rotation natively, and matches the centralized key-management pattern in lock-in #8.
   - **`OBOT_SERVER_MCPRUNTIME_BACKEND=docker`** — experiment runs on local Docker; AKS production switches to `kubernetes`.
   - **`OBOT_SERVER_ENABLE_REGISTRY_AUTH=true`** — registry API requires auth.
   - **`OBOT_SERVER_AUDIT_LOGS_MODE=disk`** — Obot writes audit events to its `/data/audit` directory (under the same `-v ${HOME}/.obot/data:/data` mount). A Vibedata-owned shim reads from disk, translates Obot's audit schema into Studio's audit format per `audit-trail/README.md`, and writes into Studio's audit store. This is the production path for capturing gateway-internal events that Studio's REST-boundary audit doesn't see (silent token refreshes, OAuth handshake details). See lock-in #7's audit row and the audit-shim future extension.
   - **`OBOT_SERVER_MCPOAUTH_CLIENT_EXPIRATION=90d`** — extends from Obot's `30d` default to reduce DCR re-registration churn (which can re-trigger user consent screens on the upstream providers).
   - **`OBOT_ENABLE_AGENTS=false`** disables Obot's chat/agent runtime explicitly (also the default for new deployments per Obot v0.22).
   - **No `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`** — Obot's chat client is off; OpenHands runs the LLM via Minimax/OpenRouter.
   - **No `OBOT_SERVER_AUTH_OWNER_EMAILS`** — Studio drives Obot admin operations via the bootstrap token followed by a `studio-system` service-account API key (see lock-in #7).
   - **`OBOT_SERVER_DISALLOW_LOCALHOST_MCP`** and **`OBOT_SERVER_MCPDEFAULT_DENY_ALL_EGRESS`** left at defaults — irrelevant to a federation-only deployment; documented in lock-in #7.
   - **No `GITHUB_AUTH_TOKEN`** — public catalog repo and low pull volume don't hit GitHub's unauth rate limit.

3. Open `http://localhost:8080`, sign in with the bootstrap token. Per lock-in #10 (local mode), **no auth provider is configured** — the bootstrap token is the developer's authentication, and `OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true` keeps it valid indefinitely.

4. In Obot's admin UI, add Linear as a remote MCP and complete its OAuth flow. The Linear refresh token is stored under the bootstrap user (the only Obot user that exists in local mode).

5. Mint an API key for the bootstrap user. Two equivalent paths:

   **a) Via the UI** (one-time, ~30 seconds): click your profile → API Keys → Create API Key. Name it `studio-local`, select "All MCP Servers", no expiration. Copy the plaintext key.

   **b) Via REST** (mirrors what Studio's installer does in production for local-mode deployments):
   ```bash
   curl -X POST http://localhost:8080/api/api-keys \
     -H "Authorization: Bearer <bootstrap-token>" \
     -H "Content-Type: application/json" \
     -d '{"name": "studio-local", "mcpServerIds": ["*"], "canAccessSkills": false}'
   ```
   Response includes the plaintext key once. Capture it.

6. Add `OBOT_URL=http://localhost:8080` and `OBOT_API_KEY=<key>` to the playground's `.env`.

## Design lock-ins for Studio production

The sample deliberately runs Obot against its **embedded Postgres** (bundled inside the Obot image) to keep Studio's existing `~/src/studio/docker-compose.yml` untouched during the experiment. The decisions below are the Studio production target the sample is validating — they are **not** the experiment's deployment shape, but they are the commitments to bake into Studio once the sample proves the approach end-to-end.

1. **Studio's bundled PG image swaps from `postgres:16-alpine` to `pgvector/pgvector:pg16`** in `~/src/studio/docker-compose.yml`. Same Postgres major version, identical credentials and volume, just the `vector` extension preinstalled at the OS package layer. Studio's existing `studio` database and schema are unaffected. Anyone needing vector features (Obot, and potentially other Studio components later) runs `CREATE EXTENSION vector` in their own database. Trivial migration cost.
2. **Shared PG cluster, separate database per service.** In production, Obot gets its own `obot` database in Studio's cluster (alongside the existing `studio` database). No second Postgres deployment. Backups and migrations treat `obot` as a peer. The experiment uses Obot's embedded PG instead; the production step is one `CREATE DATABASE obot` plus pointing Obot at the shared cluster via `OBOT_SERVER_DSN=postgres://...studio-postgres:5432/obot...`.
3. **Obot deploys as a sidecar in Studio's compose project, fronted by Studio's nginx; Obot's admin UI is never reachable externally.** In production, Obot joins `studio-net` and is reached at `http://obot:8080/mcp` by Studio's backend. **Studio's nginx fronts Obot and only proxies the paths Studio needs** — the MCP endpoint and the admin REST routes Studio's backend calls. Obot's admin UI, login pages, and any other internal surface are blocked at nginx. Studio's frontend never talks to Obot directly. The sample exposes Obot on the host at `http://localhost:8080` (no nginx in front) only because the sample script and Obot's admin UI run from the developer's machine in local mode — production drops the port-publish and adds the nginx filter.
4. **`POSTGRES_PASSWORD` reused across the cluster.** Same superuser for both databases for now. If finer-grained isolation matters later, provision an `obot` role with grants only on the `obot` database — small follow-up that doesn't break this design.
5. **OAuth is per-user; reconnect is out-of-band via Obot's user-settings UI.** Studio forces each user to OAuth-connect their own Linear (and other) accounts in their user settings before any agent action can use them. The Obot API key Studio's backend uses on the user's behalf identifies that user to Obot, and Obot scopes Linear's tokens to that user. If a tool call fails mid-conversation because of a Linear OAuth issue (token revoked, expired, consent re-required), Studio's UI deep-links the user to their user-settings reconnect flow rather than attempting an in-conversation OAuth handshake. After reconnecting, the user resumes the conversation and retries the request. No shared service-account model; no in-band OAuth driving from the agent runtime.

6. **Catalog source is `github.com/vibedata-official/mcp-catalog` (Vibedata-controlled), not Obot's upstream default.** Obot loads its MCP catalog from a Git repo at startup, controlled by `OBOT_SERVER_DEFAULT_MCPCATALOG_PATH` (default: `https://github.com/obot-platform/mcp-catalog`). Studio production overrides this to point at a Vibedata-curated catalog so editorial control over which MCP servers are visible to `vibedata_owner` admins stays with Vibedata. The configure-connectors invariant that "the catalog is the only source of connector identity" then composes naturally: Studio never invents catalog entries, Vibedata's curated repo does, and customers can't pull in arbitrary upstream MCPs without an editorial review. The system catalog (`OBOT_SERVER_DEFAULT_SYSTEM_MCPCATALOG_PATH`) is similarly pinned to Vibedata's equivalent. **The experiment uses Obot's upstream default catalog** so we can connect to Linear via the existing Docker MCP Catalog entry — switching to the Vibedata catalog is the next sample (see Future extensions).

7. **Obot configuration knobs are locked-in per the table below.** Each line is set explicitly even when it matches Obot's default, so the Studio deployment manifest is self-documenting.

   | Knob | Production value | Experiment value | Rationale |
   |---|---|---|---|
   | `OBOT_SERVER_ENABLE_AUTHENTICATION` | `true` | `true` | Multi-user gateway requires auth. |
   | `OBOT_ENABLE_AGENTS` | `false` | `false` | Chat/agent runtime is out of scope; OpenHands owns the agent. Also the v0.22 default for new deployments. |
   | `OBOT_BOOTSTRAP_TOKEN` | long-lived (the deployment-level admin token Studio uses for catalog mgmt, key revocation, etc.) | same | Verified in `pkg/bootstrap/bootstrap.go`: bootstrap token authenticates as the synthetic `bootstrap` user with Owner role. With `OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true` (next row), it stays valid indefinitely. Studio holds it; rotated on a separate ops policy. |
   | `OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP` | `true` | `true` | Keeps the bootstrap token valid even after other admin users exist. Without this, the moment any non-bootstrap Owner user is provisioned (e.g., a customer's `vibedata_owner` who signs in via GitHub) the bootstrap token silently stops working — and Studio's installer-driven admin path would break. Worth the explicit lock-in. |
   | `OBOT_SERVER_AUTH_OWNER_EMAILS` | optional (populated with the customer's `vibedata_owner` emails so they auto-receive Owner role when they sign in via GitHub) | unset | Not load-bearing for Studio's admin REST (the bootstrap token covers that). Only useful if specific humans ever need direct Obot UI access for debugging — which nginx-front blocks externally anyway (lock-in #3). Documented as available; not required. |
   | `OBOT_SERVER_HOSTNAME` | customer-domain URL reachable from end-user browsers (e.g. `https://studio.<customer>.com/obot`) | `http://localhost:8080` | Linear's consent screen redirects the user's browser to this URL — must be browser-reachable, not just backend-reachable. Set at launch, never derived. |
   | `OBOT_SERVER_ENCRYPTION_PROVIDER` | `custom` | `custom` | OAuth refresh tokens + DCR client secrets encrypted at rest. Cloud-KMS providers (`aws`/`gcp`/`azure`) are also valid for hosted Vibedata; `custom` works for self-host and composes with lock-in #8. |
   | `OBOT_SERVER_ENCRYPTION_CONFIG_FILE` | `/data/keys/obot-encryption.yaml` (mounted from `${DATA_DIR}/keys/`) | same | File path inside the container. The actual `EncryptionConfiguration` YAML (k8s format, `aescbc` provider, base64 key) lives at `${DATA_DIR}/keys/obot-encryption.yaml` on the host per lock-in #8. **Not** the inline `OBOT_SERVER_ENCRYPTION_KEY` env var — file-based keeps secrets out of env, supports the standard k8s two-key rotation flow, and matches the cross-service pattern. |
   | `OBOT_SERVER_MCPRUNTIME_BACKEND` | `kubernetes` (AKS deployment) | `docker` (local Docker for the experiment) | Per-deployment-target, not a single value. AKS uses native k8s API + auto-provisioned ServiceAccount/Role. Docker Compose uses local daemon. |
   | `OBOT_SERVER_ENABLE_REGISTRY_AUTH` | `true` | `true` | Registry API auth-gated; default `false` returns wildcard catalog reads to anyone. |
   | `OBOT_SERVER_AUDIT_LOGS_MODE` | `disk` | `disk` | Obot writes structured audit events to `/data/audit/*.jsonl`. **A Vibedata-built audit shim** tails this directory, translates Obot's event schema into Studio's audit format per `~/src/worktrees/docs/configure-connectors-fs/docs/functional/audit-trail/README.md`, and writes into Studio's audit store. The shim runs as a sidecar in production and (eventually) as a follow-up sample in this playground. REST-boundary events stay captured by Studio directly; the shim adds the gateway-internal events (silent token refreshes, DCR re-registration, OAuth protocol exchanges) so Vibedata's audit trail is complete. We picked `disk` over `s3` to keep the storage local to the Obot pod (no cloud dependency for the audit path), and over `off` so we don't lose gateway-internal events. Obot doesn't expose an OTLP audit mode today; if it ships one we can revisit and remove the shim. |
   | `OBOT_SERVER_AUDIT_LOGS_COMPRESS_FILE` | `false` (shim reads raw) | `false` | Disabled so the shim can tail JSONL line-by-line. The default `true` would compress rotated files, complicating tailing. Trade-off accepted (more disk usage) for simpler ingestion. |
   | `OBOT_SERVER_MCPOAUTH_CLIENT_EXPIRATION` | `90d` | `90d` | Extended from Obot's `30d` default. Longer DCR client lifetime means fewer re-registrations against upstream providers, which means fewer user re-consent screens. Verify max-acceptable-lifetime against Linear/Atlassian/GitHub before going longer. |
   | `OBOT_SERVER_DSN` | `postgres://...studio-postgres:5432/obot...` (per lock-ins #1-4) | unset (embedded PG) | External vs embedded; see lock-ins #1-4. |
   | `OBOT_SERVER_DEFAULT_MCPCATALOG_PATH` / `OBOT_SERVER_DEFAULT_SYSTEM_MCPCATALOG_PATH` | `github.com/vibedata-official/mcp-catalog` (and system equivalent) | unset (Obot upstream defaults) | See lock-in #6. |
   | `OBOT_SERVER_DISALLOW_LOCALHOST_MCP` | default (`false`) | default (`false`) | Federation-only catalog; no localhost endpoints in `vibedata-official` catalog by editorial policy. SSRF threat model collapses; flag adds no value. |
   | `OBOT_SERVER_MCPDEFAULT_DENY_ALL_EGRESS` | default (`false`) | default (`false`) | Only relevant when Obot launches local MCP server containers, which the federation model never does. |
   | `GITHUB_AUTH_TOKEN` | unset | unset | Public catalog repo, low pull volume, doesn't approach GitHub's 60/hour unauthenticated limit. Set only if Vibedata-hosted multi-tenant share an egress IP. |
   | `OBOT_SERVER_DISABLE_UPDATE_CHECK` | `true` (privacy / airgap-friendly enterprise customers) | `true` | Disable phone-home update check. Default `false`; flipping is operator-friendly. |

8. **All encryption keys for Vibedata services live in `${DATA_DIR}/keys/` as YAML config files.** A single per-deployment key directory holds every service's encryption material — Obot today, Studio's own encryption keys as it adopts the same pattern, future services as they're added. Each container that needs encryption mounts `${DATA_DIR}/keys/` (or a relevant subset) **read-only** at a stable in-container path (e.g. `/data/keys/`), and reads only its own file via the service-specific env var (e.g. `OBOT_SERVER_ENCRYPTION_CONFIG_FILE=/data/keys/obot-encryption.yaml`).

   **Why this pattern:**
   - **Single backup target.** Operators back up `${DATA_DIR}` and capture all encryption material in one place. No per-service key vault to discover.
   - **Single rotation surface.** Rotation procedure is the same regardless of service — write new YAML with new key as primary + old key as secondary, restart service, run service-specific storage rewrite, remove old key.
   - **Keys never in process env.** Anything that reads `/proc/<pid>/environ` or runs `docker inspect` sees a file path, not the key. Reduces accidental exposure via logs, crash dumps, OTel resource attributes, etc.
   - **Cross-service consistency.** When Studio adopts encryption-at-rest for its own DB columns, the same `${DATA_DIR}/keys/studio-encryption.yaml` slot is waiting. No new pattern per service.

   **File-naming convention:** `${DATA_DIR}/keys/<service>-encryption.yaml`. Examples: `obot-encryption.yaml`, `studio-encryption.yaml`, `<future-service>-encryption.yaml`.

   **Format:** Kubernetes `apiVersion: apiserver.config.k8s.io/v1` `EncryptionConfiguration` YAML. Obot uses this natively (their `aws-encryption.yaml`, `azure-encryption.yaml`, `gcp-encryption.yaml` templates in the upstream repo follow this format); Studio adopts the same format for its own keys so a single rotation tool can manage all of them. Format example for the `custom` provider:

   ```yaml
   apiVersion: apiserver.config.k8s.io/v1
   kind: EncryptionConfiguration
   resources:
     - resources: [<resource-types>]
       providers:
         - aescbc:
             keys:
               - name: vibedata-<service>-key-1
                 secret: <base64-encoded 32-byte key>
         - identity: {}  # fallback for migration from unencrypted state
   ```

   **Mount mode:** read-only. Obot does not write to its encryption config; neither will any future Vibedata service.

   **Permissions:** `chmod 600` on every key file; ownership matches the host user that operates Vibedata. Container runtime preserves the file's permissions inside the mount.

   **Key generation:** `openssl rand -base64 32` for AES-CBC-256. Out of scope for this lock-in: KMS-backed alternatives (`aws`/`gcp`/`azure` Obot providers) — those are an option for hosted Vibedata; self-host customers use `custom` with this file pattern.

9. **Studio production wires OTel end-to-end across the OpenHands → Obot → remote-MCP boundary.** Studio's compose already exports OTel to its bundled Alloy/Tempo stack (`OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy:4318`); the Obot sidecar inherits the same env so its traces, metrics, and logs land in Studio's existing collector. Span continuity across the agent → gateway hop relies on W3C TraceContext: OpenHands' MCP client must inject `traceparent` headers on outgoing `streamable-http` requests, and Obot picks them up via its global propagator (`pkg/services/otel.go` already sets this up). If OpenHands' fastmcp client doesn't auto-inject, Studio wraps the transport — small one-time fix on Studio's side.

   **The sample omits all of this** because the OTel wiring is not the load-bearing question we're evaluating. The sample tests MCP plumbing, dispatcher behavior, and OAuth recovery; OTel is an orthogonal pipeline Studio is already running for its own services and that Obot already supports natively. Treating OTel as solved-by-existing-infra in the sample keeps the evaluation focused. Studio's first Obot rollout adds the env-var wiring and verifies the cross-boundary trace continuity as part of Studio's own integration work, not as a separate sample.

10. **Studio's auth model with Obot splits by deployment mode.** Two production-shipping modes, two auth surfaces, one shared evaluation harness (the sample we're building).

   **Local source / local Docker mode (single developer):**
   - Obot runs with `OBOT_SERVER_ENABLE_AUTHENTICATION=true` and `OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true`. No auth provider configured (no GitHub OAuth app required).
   - One-time per deployment: Studio's installer (or the developer running the sample today) hits Obot's bootstrap-token-authenticated `POST /api/api-keys` to mint one API key for the `bootstrap` user, scoped to all MCP servers (`"mcpServerIds": ["*"]`), no expiration.
   - The plaintext key is written to `${DATA_DIR}/.env` (or `${DATA_DIR}/obot/api-key`) on the host. Studio's backend Docker container reads it via env (`OBOT_API_KEY`) at startup.
   - All MCP traffic — for any operation, by any code path — uses that one key. The bootstrap user holds every upstream OAuth grant (Linear, etc.). Scope is single-developer; per-user Linear isolation is meaningless because there's only one user.
   - **This is exactly what the sample we're building validates.**

   **K8s production mode (multi-user):**
   - Single shared GitHub OAuth app per deployment, registered in the customer's GitHub org during setup. Callback URLs configured for both Studio's and Obot's paths (nginx routes Obot's callback through the same domain — see lock-in #3).
   - Obot's `github-auth-provider` is configured by Studio's installer via the bootstrap-token-authenticated admin REST during deployment — same `client_id` + `client_secret` Studio uses.
   - **Per-user mint flow:** user signs into Studio via GitHub → on first Obot need, Studio redirects through Obot's GitHub login (auto-approved by GitHub since the same OAuth app is already authorized) → Obot session established → Studio's backend calls `POST /api/api-keys` with the user's session → captures the plaintext key once → stores encrypted (per lock-in #8) in Studio's database, keyed on `{user_id, obot_instance_id}` → reuses for all subsequent MCP traffic on that user's behalf. Rotation on a 90-day policy via `DELETE /api/api-keys/{id}` + remint.
   - The bootstrap token (still long-lived via `OBOT_SERVER_FORCE_ENABLE_BOOTSTRAP=true`) handles deployment-level admin operations: catalog enable/disable, key revocation across all users, user listing, role management. Studio's installer/backend holds it; end users never see it.
   - **Per-user OAuth (lock-in #5) is preserved**: each customer end user has their own Obot identity, their own Linear refresh token, their own scoped tool universe.
   - **Not validated by this sample.** A follow-up sample exercises the first-call mint flow with a simulated shared-SSO session.

The sample is the test bed for these commitments. If anything proves wrong during implementation (e.g., Obot has an undocumented PG17 dependency we hit on PG16 migrations, the per-user reconnect flow surfaces UX gaps, DCR re-registration triggers consent more aggressively than `90d` suggests, OpenHands' fastmcp client doesn't propagate `traceparent` when Studio wires OTel, the GitHub auth provider's admin-REST configuration interface differs from the REST surface, or the key-file format diverges in a future Obot release), we adjust before this lands in Studio.

## Error handling

| Condition | Behavior |
|---|---|
| `OBOT_API_KEY` not set in env | Sample raises `SystemExit("OBOT_API_KEY not set; see docs/superpowers/specs/...")` before constructing the Agent. |
| Obot unreachable at `OBOT_URL` | The first `conversation.run()` surfaces an MCP transport error from fastmcp. Sample catches and prints `(MCP connection failed: <err>) — is Obot running on {OBOT_URL}?`. REPL continues so the user can fix and retry. |
| `OBOT_API_KEY` invalid / expired | Obot returns 401 on the MCP call. fastmcp raises. Sample prints `(Obot rejected the API key; regenerate one in the Obot UI)`. REPL continues. |
| Linear not connected in Obot | The agent sees no Linear tools (or sees a stub if Obot advertises catalog entries regardless). Either is acceptable — the sample lets the LLM respond naturally. `/status` reveals the gap. |
| Linear's upstream token expired / revoked / consent required mid-conversation | Tool call returns an observation containing an OAuth-class error string. The `print_oauth_banner_on_failure` callback detects it and prints a prominent reconnect banner above the agent's normal output, pointing at `http://localhost:8080/user-settings/connectors/linear`. The observation also reaches the agent, which surfaces the failure in its own response (likely a `FinishAction` saying it couldn't access Linear). REPL stays alive; user reconnects in Obot's UI out of band and retypes the request — same shape Studio's UX will use (deep-link the user to user-settings, user retries). Per-user OAuth: only this user's tokens are involved; no other users' state is affected. |
| `/connect <connector>` typed | Print the reconnect URL for the named connector (default Linear path: `http://localhost:8080/user-settings/connectors/linear`). No in-band OAuth. Diagnostic shortcut that produces the same URL the failure callback would print. |
| Other slash command typed (e.g. `/recap`, `/foo`) | Treated as a normal user message (no command parsing beyond `/quit`, `/exit`, `/status`, `/connect`). |
| `KeyboardInterrupt` at the prompt | Clean exit, no traceback. Matches the pattern from `samples/recap.py`. |

## Recap of what the sample teaches Studio

By the end of the sample being runnable end-to-end, the following questions are answered with code, not speculation:

1. **Does OpenHands' fastmcp client talk to Obot's `streamable-http` endpoint cleanly?** Validates the production runtime plumbing Studio will rely on.
2. **What does MCP-client authentication look like against Obot?** API key in `Authorization: Bearer` header is the working hypothesis; the sample either confirms or surfaces the correct shape on first run. Studio's backend will use the same.
3. **Does Obot expose individual MCP tools (e.g., `mcp__obot__linear_create_issue`) or a dispatcher pattern (e.g., `mcp__obot__find` / `mcp__obot__exec`)?** Material to Studio's system-message footprint story — the configure-connectors spec asks for constant footprint via a dispatcher; this sample reveals whether Obot's `streamable-http` surface already solves that or whether Studio still needs to layer a dispatcher on top.
4. **What is the actual REST surface Studio's backend will need to call?** `/status` exercises one corner of it; the impl pass will discover the catalog/connection endpoints.
6. **What does latency feel like for a tool round-trip?** Quantitative read on the per-call overhead of routing OpenHands → Obot → Linear vs a direct Linear connection.

## Open questions surfaced (resolved during implementation, not blockers)

- **Exact Obot MCP endpoint path.** `/mcp`, `/v1/mcp`, `/api/mcp`, or something else. First run reveals.
- **Exact Obot API-key header shape.** `Authorization: Bearer <token>` is the convention; Obot may use a custom header. First run reveals.
- **Obot's REST endpoints for catalog/connection listing.** `obot_status()` discovers and documents them.

## Testing

This is a playground sample. Verification is manual:

1. `uv run python samples/mcp_obot_linear_repl.py` starts and prompts.
2. Type `/status` — prints the connected MCPs and available tools. Confirms the MCP control plane is reachable.
3. Type `/connect linear` — prints the reconnect URL (`http://localhost:8080/user-settings/connectors/linear`). Confirms the diagnostic shortcut works on demand.
4. Type a real instruction (`Create a Linear issue in my Inbox project titled "test from sample"`). The agent uses a Linear tool through Obot and reports success.
5. **OAuth recovery path.** Deliberately break the Linear connection — easiest path is to revoke the OAuth grant in Linear's own connected-apps settings, or click Disconnect on Linear inside Obot's UI. Then retype the instruction from step 4. Expected: the tool call fails, the agent reports it couldn't access Linear, and the sample prints the OAuth reconnect banner pointing at `http://localhost:8080/user-settings/connectors/linear`. The REPL stays alive. Reconnect Linear in Obot's UI, retype the instruction, and confirm it succeeds. This validates the production UX path Studio will deep-link users into.
6. `Ctrl-C` at the prompt exits cleanly; `/quit` / `/exit` exit cleanly.

No automated tests. The playground does not have a test harness, and adding one for one sample is out of scope.

## Future extensions (not in scope)

- **Vibedata-controlled catalog from a Git repo.** Follow-up sample that points Obot at `github.com/vibedata-official/mcp-catalog` via `OBOT_SERVER_DEFAULT_MCPCATALOG_PATH` (and the system equivalent), validates that the curated entries appear in `docker mcp catalog server ls` / Obot's admin UI and uncurated upstream entries do not, and runs a small lifecycle test (push a new entry to the catalog repo → restart Obot → entry appears; remove an entry → restart → entry withdraws). Exercises the editorial-control story Studio's `vibedata_owner` surface depends on. Locks in the catalog-repo URL, the system-catalog vs default-catalog separation, and the catalog-refresh ergonomics.
- **Obot audit-shim sample.** Self-contained Python utility (`samples/obot_audit_shim.py`) that tails `/data/audit/*.jsonl`, parses each Obot audit event, translates the schema into Studio's audit format per `~/src/worktrees/docs/configure-connectors-fs/docs/functional/audit-trail/README.md`, and writes into Studio's audit store (or stdout in dry-run mode). Demonstrates the production audit pipeline end-to-end: gateway-internal events → disk → translation → Studio audit store. Likely uses `watchdog` or simple polling for the tail loop. Validates the schema mapping before Studio's production sidecar gets built.
- **Native OTLP mode for `OBOT_SERVER_AUDIT_LOGS_MODE` (upstream feature request).** Obot already wires up an OTel logger provider for operational logs; adding `otlp` as an audit-logs backend would let audit events flow through the same pipeline as traces and metrics, removing the disk-shim hop. File an upstream issue if/when Vibedata's audit pipeline benefits from collapsing the shim. Not the chosen path today (lock-in #7 picks `disk` + shim); track as a deferred optimization.
- **Production-mode auth demo.** Self-contained Python sample that simulates K8s production-mode auth: stand up Obot with `github-auth-provider` configured, simulate a shared-SSO session for a test user, exercise the first-call `POST /api/api-keys` mint flow, store + reuse the key. Validates lock-in #10's K8s mode end-to-end before Studio ships it.
- **Multi-user OAuth demo** (two test users, two parallel OpenHands conversations, distinct Linear identities). Validates the per-user OAuth lock-in (#5) end-to-end. Composes with the production-mode auth sample above.
- **Per-intent allow-list filtering enforced by Obot.** Studio concern; reaches into the dispatcher's allowed-universe story.
- **ContextForge spike as a head-to-head with Obot.** Vendor-comparison work, not pattern-evaluation work.
- **Replacing the REPL with a longer-running agent** that drives a multi-step Linear workflow (issue → comment → state change). Useful but orthogonal.
- **TLS termination, persistence, multi-host deployment.** All real Vibedata-as-product concerns; out of scope here.
