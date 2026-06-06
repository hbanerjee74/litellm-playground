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
   - Empty line → continue.
   - Anything else → `conversation.send_message(line)` + `conversation.run()`.

### File layout impact

- New file: `samples/mcp_obot_linear_repl.py`.
- `.env.example` adds: `OBOT_URL=http://localhost:8080`, `OBOT_API_KEY=`, `OTEL_EXPORTER_OTLP_ENDPOINT=` (commented out).
- `pyproject.toml`: add `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-httpx` as runtime deps (only loaded if OTel env var is set, but always installed). `openhands-sdk`, `python-dotenv` already present.

## Out-of-band setup the user does once before running

1. **Run Obot:**
   ```bash
   docker run -d --name obot -p 8080:8080 \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -e OPENAI_API_KEY=$OPENAI_API_KEY \
     -e OBOT_SERVER_ENABLE_AUTHENTICATION=true \
     -e OBOT_BOOTSTRAP_TOKEN=<bootstrap-token> \
     ghcr.io/obot-platform/obot:latest
   ```
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

## Error handling

| Condition | Behavior |
|---|---|
| `OBOT_API_KEY` not set in env | Sample raises `SystemExit("OBOT_API_KEY not set; see docs/superpowers/specs/...")` before constructing the Agent. |
| Obot unreachable at `OBOT_URL` | The first `conversation.run()` surfaces an MCP transport error from fastmcp. Sample catches and prints `(MCP connection failed: <err>) — is Obot running on {OBOT_URL}?`. REPL continues so the user can fix and retry. |
| `OBOT_API_KEY` invalid / expired | Obot returns 401 on the MCP call. fastmcp raises. Sample prints `(Obot rejected the API key; regenerate one in the Obot UI)`. REPL continues. |
| Linear not connected in Obot | The agent sees no Linear tools (or sees a stub if Obot advertises catalog entries regardless). Either is acceptable — the sample lets the LLM respond naturally. `/status` reveals the gap. |
| Linear's upstream token expired | Tool call returns an MCP error from Obot. Sample lets the error propagate to the agent's working memory; the agent can surface the issue to the user. Remediation: reconnect Linear in Obot's UI. |
| `/recap` / unknown slash command typed | Treated as a normal user message (no command parsing beyond `/quit`, `/exit`, `/status`). |
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
3. Type a real instruction (`Create a Linear issue in my Inbox project titled "test from sample"`). The agent should use a Linear tool through Obot and report success.
4. If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, visit the collector UI and confirm the trace shows OpenHands → Obot → Linear spans linked under one trace ID.
5. `Ctrl-C` at the prompt exits cleanly; `/quit` / `/exit` exit cleanly.

No automated tests. The playground does not have a test harness, and adding one for one sample is out of scope.

## Future extensions (not in scope)

- Multi-user OAuth demo (two test users, two parallel OpenHands conversations, distinct Linear identities). This is the next sample on the roadmap.
- Per-intent allow-list filtering enforced by Obot. Studio concern.
- ContextForge spike as a head-to-head with Obot. Vendor-comparison work, not pattern-evaluation work.
- Replacing the REPL with a longer-running agent that drives a multi-step Linear workflow (issue → comment → state change). Useful but orthogonal.
- TLS termination, persistence, multi-host deployment. All real Vibedata-as-product concerns; out of scope here.
