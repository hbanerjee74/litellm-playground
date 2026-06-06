# Obot Gateway + Linear MCP + OpenHands REPL — Design

**Date:** 2026-06-06
**Status:** Draft, pending implementation
**Scope:** Add a single self-contained OpenHands SDK sample that consumes a remote MCP (Linear) through the Obot gateway. Evaluation harness for the configure-connectors design landing in Studio (`~/src/studio`); the sample exercises the integration boundary Studio will own.

## Goal

Stand up Obot locally, federate Linear through it (OAuth already mediated by Obot's UI), and drive a Python REPL that uses OpenHands' SDK to call Linear tools through Obot. The sample is the cheapest way to surface the load-bearing integration questions for the Studio production design: MCP-client authentication shape, gateway dispatcher vs per-tool advertisement, OTel span propagation across the agent → gateway → remote-MCP boundary, and the REST surface Studio's backend will call.

## Non-goals

- Multi-user OAuth in code. Obot supports it natively (per-user OAuth tokens, IdP integration, OAuth diagnostics) but the sample exercises a single user (the developer). Multi-user is the *next* sample on the roadmap (two parallel intents, two MCPs, one gateway).
- Per-intent allow-list filtering. Studio concern; not exercised here.
- Driving OAuth from the sample. Obot's admin UI does this correctly; reimplementing it adds no information.
- Building our own dispatcher. We see what Obot already exposes through its `streamable-http` surface and judge whether per-tool advertisement is acceptable for Studio's system-message footprint or whether a dispatcher layer is still needed.
- Productionization (TLS termination, persistence, multi-tenant scaling). Single-host local eval only.
- Comparing gateways head-to-head in code. The vendor evaluation lives in the brainstorm; this sample commits to Obot.

## Key external facts (verified)

- **Obot exposes everything via `streamable-http` transport, regardless of the underlying server runtime.** (Quoted from Obot's `concepts/mcp-gateway.md`.) Single transport surface, even for stdio MCP servers that Obot itself hosts.
- **OpenHands' MCP client (fastmcp) supports `streamable-http` natively.** `fastmcp.mcp_config.RemoteMCPServer.transport` accepts `Literal["http", "streamable-http", "sse"]`.
- **Obot ships first-class OTel.** `pkg/services/otel.go` uses `go.opentelemetry.io/contrib/exporters/autoexport`, configured via standard `OTEL_*` env vars. W3C TraceContext + Baggage propagators are set globally. Traces, metrics, and logs are all emitted.
- **Obot's gateway authenticates each user and proxies with that identity.** Per-user OAuth tokens; admins can deploy one shared multi-user MCP and have each user supply per-user header values that pass through to the upstream MCP.

## Architecture

A single file: `samples/mcp_obot_linear_repl.py`. Runnable directly via `uv run python samples/mcp_obot_linear_repl.py`.

```
┌──────────────────────────────────────────┐
│ samples/mcp_obot_linear_repl.py          │  uv run, REPL loop
│  • OpenHands LLM (Minimax via OpenRouter)│
│  • Agent + Conversation                  │
│  • MCPConfig → Obot (streamable-http)    │
│  • Optional OTel SDK init                │
└──────────────────┬───────────────────────┘
                   │  streamable-http
                   │  (traceparent + Authorization headers)
                   ▼
┌──────────────────────────────────────────┐
│ Obot (docker run, port 8080)             │  external, set up out-of-band
│  • Linear federated via admin UI         │
│  • Per-user OAuth tokens, API-key auth   │
│  • OTel autoexport → collector if set    │
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

4. **Optional OTel bootstrap.** If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the sample initializes the Python OTel SDK at the top of `main()`:
   - `TracerProvider` with `OTLPSpanExporter` (autoexport-equivalent — gRPC or HTTP based on env)
   - `TextMapPropagator` set to `TraceContextTextMapPropagator + W3CBaggagePropagator`
   - HTTPX instrumentation so outgoing MCP requests automatically include `traceparent` headers
   - Resource attributes: `service.name=mcp-obot-linear-repl`, `service.version=0.1.0`
   If the env var is unset, OTel is a no-op — sample still runs.

5. **`obot_status()` helper.** `GET {OBOT_URL}/api/<TBD-on-first-run>` with the Bearer token, prints the user's connected MCPs and the catalog of available servers. Implements the `/status` slash command.

6. **REPL loop.** Identical shape to `samples/recap.py`:
   - Clean `^C` exit via SIGINT handler raising `KeyboardInterrupt`.
   - `> ` prompt.
   - `/quit` / `/exit` / EOF / `KeyboardInterrupt` → exit.
   - `/status` → `obot_status()`.
   - `/connect <connector>` → print the reconnect URL for the named connector (e.g. `http://localhost:8080/user-settings/connectors/linear`). No in-band OAuth driving; the URL points the user at Obot's existing user-settings flow. Used both proactively and as the diagnostic the sample prints automatically when it detects an OAuth-class tool failure (see [Error handling](#error-handling)).
   - Empty line → continue.
   - Anything else → `conversation.send_message(line)` + `conversation.run()`.

7. **`print_oauth_banner_on_failure(event)` callback.** Registered as a `Conversation` callback. Inspects every `ObservationEvent`; when the observation text matches OAuth-class failure patterns (`401`, `Unauthorized`, `OAuth`, `token expired`, `consent required`, `invalid_token`), prints a prominent banner above the agent's normal output:
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
- `.env.example` adds: `OBOT_URL=http://localhost:8080`, `OBOT_API_KEY=`, `OTEL_EXPORTER_OTLP_ENDPOINT=` (commented out).
- `pyproject.toml`: add `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-httpx` as runtime deps (only loaded if OTel env var is set, but always installed). `openhands-sdk`, `python-dotenv` already present.

## Out-of-band setup the user does once before running

The sample uses **Obot's embedded Postgres** (bundled inside the Obot image, pgvector preinstalled). No standalone PG container, no modification to Studio's compose. This is the minimum-friction deployment for the experiment; the production deployment shape — swapping Studio's PG image and sharing the cluster — is captured under [Design lock-ins for Studio production](#design-lock-ins-for-studio-production) as the target Studio will move to once this approach is validated.

1. **Run Obot** (LLM API key not required — OpenHands runs the LLM, Obot is only the gateway):
   ```bash
   docker run -d --name obot -p 8080:8080 \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v ${HOME}/.obot/data:/data \
     -e OBOT_SERVER_ENABLE_AUTHENTICATION=true \
     -e OBOT_ENABLE_AGENTS=false \
     -e OBOT_BOOTSTRAP_TOKEN=<bootstrap-token> \
     ghcr.io/obot-platform/obot:latest
   ```
   Notes:
   - No `OBOT_SERVER_DSN` — without it, Obot uses its embedded Postgres (running inside the container). pgvector is already installed in the embedded image; no extra provisioning needed.
   - The `-v ${HOME}/.obot/data:/data` mount persists the embedded PG's data dir across container restarts. Drop the mount if you want a fully ephemeral experiment.
   - `OBOT_ENABLE_AGENTS=false` disables Obot's chat/agent runtime explicitly (also the default for new deployments per Obot v0.22). The gateway and MCP routing paths are unaffected.
   - No `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — Obot's chat client is off; OpenHands runs the LLM via Minimax/OpenRouter.

2. Open `http://localhost:8080`, sign in with the bootstrap token.

3. In Obot's admin UI: configure an auth provider (use the local-dev provider for the sample), add Linear as a remote MCP, complete OAuth at Linear.

4. Generate an API key for the developer user (exact UI path TBD; documented during impl).

5. Add `OBOT_URL=http://localhost:8080` and `OBOT_API_KEY=<key>` to the playground's `.env`.

Optional, for OTel:

6. Run a local OTLP collector:
   ```bash
   docker run -d --name jaeger -p 4317:4317 -p 4318:4318 -p 16686:16686 \
     jaegertracing/all-in-one:latest
   ```
7. Add `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` to `.env`.
8. Visit `http://localhost:16686` after running the REPL to view linked traces.

## Design lock-ins for Studio production

The sample deliberately runs Obot against its **embedded Postgres** (bundled inside the Obot image) to keep Studio's existing `~/src/studio/docker-compose.yml` untouched during the experiment. The decisions below are the Studio production target the sample is validating — they are **not** the experiment's deployment shape, but they are the commitments to bake into Studio once the sample proves the approach end-to-end.

1. **Studio's bundled PG image swaps from `postgres:16-alpine` to `pgvector/pgvector:pg16`** in `~/src/studio/docker-compose.yml`. Same Postgres major version, identical credentials and volume, just the `vector` extension preinstalled at the OS package layer. Studio's existing `studio` database and schema are unaffected. Anyone needing vector features (Obot, and potentially other Studio components later) runs `CREATE EXTENSION vector` in their own database. Trivial migration cost.
2. **Shared PG cluster, separate database per service.** In production, Obot gets its own `obot` database in Studio's cluster (alongside the existing `studio` database). No second Postgres deployment. Backups and migrations treat `obot` as a peer. The experiment uses Obot's embedded PG instead; the production step is one `CREATE DATABASE obot` plus pointing Obot at the shared cluster via `OBOT_SERVER_DSN=postgres://...studio-postgres:5432/obot...`.
3. **Obot deploys as a sidecar in Studio's compose project.** In production, Obot joins `studio-net` and is reached at `http://obot:8080/mcp` by Studio's backend; not exposed externally. Studio's frontend never talks to Obot directly. The sample exposes Obot on the host at `http://localhost:8080` only because the sample script and Obot's admin UI run from the developer's machine; this port-publish goes away in production.
4. **`POSTGRES_PASSWORD` reused across the cluster.** Same superuser for both databases for now. If finer-grained isolation matters later, provision an `obot` role with grants only on the `obot` database — small follow-up that doesn't break this design.
5. **OAuth is per-user; reconnect is out-of-band via Obot's user-settings UI.** Studio forces each user to OAuth-connect their own Linear (and other) accounts in their user settings before any agent action can use them. The Obot API key Studio's backend uses on the user's behalf identifies that user to Obot, and Obot scopes Linear's tokens to that user. If a tool call fails mid-conversation because of a Linear OAuth issue (token revoked, expired, consent re-required), Studio's UI deep-links the user to their user-settings reconnect flow rather than attempting an in-conversation OAuth handshake. After reconnecting, the user resumes the conversation and retries the request. No shared service-account model; no in-band OAuth driving from the agent runtime.

The sample is the test bed for these commitments. If anything proves wrong during implementation (e.g., Obot has an undocumented PG17 dependency we hit on PG16 migrations, or the per-user reconnect flow surfaces unexpected UX gaps), we adjust before this lands in Studio.

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
| OTel SDK initialization fails (e.g., collector unreachable) | Sample logs a warning and continues without tracing. Never fatal. |

## Recap of what the sample teaches Studio

By the end of the sample being runnable end-to-end, the following questions are answered with code, not speculation:

1. **Does OpenHands' fastmcp client talk to Obot's `streamable-http` endpoint cleanly?** Validates the production runtime plumbing Studio will rely on.
2. **What does MCP-client authentication look like against Obot?** API key in `Authorization: Bearer` header is the working hypothesis; the sample either confirms or surfaces the correct shape on first run. Studio's backend will use the same.
3. **Does Obot expose individual MCP tools (e.g., `mcp__obot__linear_create_issue`) or a dispatcher pattern (e.g., `mcp__obot__find` / `mcp__obot__exec`)?** Material to Studio's system-message footprint story — the configure-connectors spec asks for constant footprint via a dispatcher; this sample reveals whether Obot's `streamable-http` surface already solves that or whether Studio still needs to layer a dispatcher on top.
4. **Does the OTel trace span across OpenHands → Obot → Linear cleanly?** If yes, Studio's observability story is solved by Obot's existing instrumentation plus standard `OTEL_*` env vars at the OpenHands process. If no, Studio needs to inject `traceparent` at the MCP transport layer.
5. **What is the actual REST surface Studio's backend will need to call?** `/status` exercises one corner of it; the impl pass will discover the catalog/connection endpoints.
6. **What does latency feel like for a tool round-trip?** Quantitative read on the per-call overhead of routing OpenHands → Obot → Linear vs a direct Linear connection.

## Open questions surfaced (resolved during implementation, not blockers)

- **Exact Obot MCP endpoint path.** `/mcp`, `/v1/mcp`, `/api/mcp`, or something else. First run reveals.
- **Exact Obot API-key header shape.** `Authorization: Bearer <token>` is the convention; Obot may use a custom header. First run reveals.
- **Whether OpenHands' fastmcp client emits `traceparent` headers automatically.** If yes, OTel is plug-and-play. If no, the sample wraps the transport to inject headers. Either is small.
- **Obot's REST endpoints for catalog/connection listing.** `obot_status()` discovers and documents them.

## Testing

This is a playground sample. Verification is manual:

1. `uv run python samples/mcp_obot_linear_repl.py` starts and prompts.
2. Type `/status` — prints the connected MCPs and available tools. Confirms the MCP control plane is reachable.
3. Type `/connect linear` — prints the reconnect URL (`http://localhost:8080/user-settings/connectors/linear`). Confirms the diagnostic shortcut works on demand.
4. Type a real instruction (`Create a Linear issue in my Inbox project titled "test from sample"`). The agent uses a Linear tool through Obot and reports success.
5. If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, visit the collector UI and confirm the trace shows OpenHands → Obot → Linear spans linked under one trace ID.
6. **OAuth recovery path.** Deliberately break the Linear connection — easiest path is to revoke the OAuth grant in Linear's own connected-apps settings, or click Disconnect on Linear inside Obot's UI. Then retype the instruction from step 4. Expected: the tool call fails, the agent reports it couldn't access Linear, and the sample prints the OAuth reconnect banner pointing at `http://localhost:8080/user-settings/connectors/linear`. The REPL stays alive. Reconnect Linear in Obot's UI, retype the instruction, and confirm it succeeds. This validates the production UX path Studio will deep-link users into.
7. `Ctrl-C` at the prompt exits cleanly; `/quit` / `/exit` exit cleanly.

No automated tests. The playground does not have a test harness, and adding one for one sample is out of scope.

## Future extensions (not in scope)

- Multi-user OAuth demo (two test users, two parallel OpenHands conversations, distinct Linear identities). This is the next sample on the roadmap.
- Per-intent allow-list filtering enforced by Obot. Studio concern.
- ContextForge spike as a head-to-head with Obot. Vendor-comparison work, not pattern-evaluation work.
- Replacing the REPL with a longer-running agent that drives a multi-step Linear workflow (issue → comment → state change). Useful but orthogonal.
- TLS termination, persistence, multi-host deployment. All real Vibedata-as-product concerns; out of scope here.
