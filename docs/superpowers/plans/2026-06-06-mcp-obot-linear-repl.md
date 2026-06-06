# Obot MCP Gateway + Linear + OpenHands REPL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `samples/mcp_obot_linear_repl.py` — a single self-contained OpenHands SDK REPL that consumes a remote MCP (Linear) through the Obot gateway. Evaluation harness for the configure-connectors design that will land in Studio.

**Architecture:** Plain blocking `input()` REPL on the main thread, identical shape to `samples/recap.py` and `samples/critic_refinement.py`. The agent's MCP client is configured via `fastmcp.mcp_config.MCPConfig` pointing at Obot's `/mcp` endpoint over `streamable-http` transport with a Bearer API-key header. Slash commands `/status` and `/connect <connector>` give diagnostics; a `Conversation` callback detects OAuth-class errors in observation events and prints a prominent reconnect banner pointing at Obot's user-settings deep-link. OTel cross-boundary trace propagation is deferred to Studio production per spec lock-in #9; the sample omits it.

**Tech Stack:** Python ≥ 3.12, `openhands-sdk` (>= 1.24.0), `openhands-tools`, `fastmcp` (vendored by openhands-sdk), `python-dotenv`. `uv` for dependency / run management.

**Spec:** `docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md`

**Note on testing:** Per the spec, this is a playground sample with no test harness — there are no automated tests. Each task ends with a parse/import smoke test plus a commit. Manual verification (running the live REPL against a real Obot + Linear OAuth) is gathered into Task 5 at the end. The out-of-band Obot setup (docker run, admin UI, Linear OAuth, API key) is documented in the spec and is the user's responsibility before Task 5.

---

## File Structure

- **Create:** `samples/mcp_obot_linear_repl.py` — entire sample lives here. Single file matches the existing one-sample-one-file convention from `samples/recap.py` and `samples/critic_refinement.py`.
- **Modify:** `.env.example` — add `OBOT_URL`, `OBOT_API_KEY` declarations.
- **No `pyproject.toml` changes** — the sample uses only already-installed deps (`openhands-sdk`, `openhands-tools`, `python-dotenv`).

---

## Task 1: Skeleton — REPL + LLM/Agent/Conversation, no MCP yet

**Goal:** A working REPL where the user can talk to an OpenHands agent backed by Minimax/OpenRouter, no Obot wiring yet. Confirms the agent loop works in isolation before introducing MCP, so any later MCP-related failure is clearly attributable to the Obot wiring, not OpenHands itself.

**Files:**
- Create: `samples/mcp_obot_linear_repl.py`
- Modify: `.env.example`

- [ ] **Step 1: Verify dependencies and env vars are already in place**

Run:
```bash
grep -E "openhands-sdk|openhands-tools|python-dotenv" pyproject.toml
grep OPENROUTER_API_KEY .env.example
```
Expected: each grep prints a matching line. If not, stop — the spec assumes these are present.

- [ ] **Step 2: Append new env-var declarations to `.env.example`**

Append these lines to `/Users/hbanerjee/src/litellm-playground/.env.example`:

```
OBOT_URL=http://localhost:8080
OBOT_API_KEY=
```

- [ ] **Step 3: Create `samples/mcp_obot_linear_repl.py` with the skeleton**

Write this exact content to `/Users/hbanerjee/src/litellm-playground/samples/mcp_obot_linear_repl.py`:

```python
"""OpenHands + Obot MCP Gateway + Linear REPL.

Evaluation harness for the configure-connectors design landing in Studio.
See docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md for
the full design, lock-ins for Studio production, and the out-of-band Obot
setup steps (Obot must be running locally and Linear authorized through
Obot's admin UI before this sample is useful).
"""

from __future__ import annotations

import os
import signal
import tempfile

from dotenv import load_dotenv
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm


def main() -> None:
    load_dotenv()

    # Clean ^C exit, matching set_confirmation_policy.py / recap.py.
    signal.signal(
        signal.SIGINT,
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    obot_url = os.environ.get("OBOT_URL", "http://localhost:8080")

    llm = LLM(
        model="openrouter/z-ai/glm-5.1",
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url="https://openrouter.ai/api/v1",
    )
    agent = Agent(llm=llm)
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(prefix="obot_demo_"),
    )
    conversation.set_confirmation_policy(NeverConfirm())

    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "Send a message to the agent. Slash commands: /quit, /exit.\n"
        "(MCP wiring lands in Task 2; this skeleton has no Obot tools.)\n"
    )

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if line in ("/quit", "/exit"):
            break
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Confirm the file parses and imports resolve**

Run:
```bash
uv run python -c "import ast; ast.parse(open('samples/mcp_obot_linear_repl.py').read()); print('parse ok')"
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cr', 'samples/mcp_obot_linear_repl.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('import ok')
"
```
Expected: both print `... ok`. The OpenHands SDK banner may also appear; that is fine.

- [ ] **Step 5: Commit**

```bash
git add .env.example samples/mcp_obot_linear_repl.py
git commit -m "Add Obot+Linear REPL skeleton: REPL with LLM/Agent/Conversation, no MCP yet"
```

---

## Task 2: Wire MCPConfig pointing at Obot

**Goal:** The agent now talks to Obot over `streamable-http` and surfaces Obot-mediated tools (Linear tools when Linear is authorized in Obot's admin UI). `OBOT_API_KEY` becomes required.

**Files:**
- Modify: `samples/mcp_obot_linear_repl.py`

- [ ] **Step 1: Add the `MCPConfig` import**

In `samples/mcp_obot_linear_repl.py`, find:

```python
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm
```

Replace with:

```python
from fastmcp.mcp_config import MCPConfig

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm
```

- [ ] **Step 2: Add `OBOT_API_KEY` env-var validation**

In `samples/mcp_obot_linear_repl.py`, find:

```python
    obot_url = os.environ.get("OBOT_URL", "http://localhost:8080")
```

Replace with:

```python
    obot_url = os.environ.get("OBOT_URL", "http://localhost:8080")
    obot_api_key = os.environ.get("OBOT_API_KEY")
    if not obot_api_key:
        raise SystemExit(
            "OBOT_API_KEY is not set. Generate an API key in Obot's admin UI "
            "(http://localhost:8080) and add it to .env. See "
            "docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md "
            "for full out-of-band setup steps."
        )
```

- [ ] **Step 3: Build the `MCPConfig` and pass it to the Agent**

In `samples/mcp_obot_linear_repl.py`, find:

```python
    agent = Agent(llm=llm)
```

Replace with:

```python
    mcp_config = MCPConfig.model_validate({
        "mcpServers": {
            "obot": {
                "transport": "streamable-http",
                "url": f"{obot_url}/mcp",
                "headers": {"Authorization": f"Bearer {obot_api_key}"},
            }
        }
    })

    agent = Agent(llm=llm, mcp_config=mcp_config)
```

Note: the `/mcp` URL path is the spec's working assumption (Obot's documented MCP endpoint); if it turns out to be different on first run against a real Obot, this is the one line to change.

- [ ] **Step 4: Update the banner to reflect MCP is now wired**

In `samples/mcp_obot_linear_repl.py`, find:

```python
    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "Send a message to the agent. Slash commands: /quit, /exit.\n"
        "(MCP wiring lands in Task 2; this skeleton has no Obot tools.)\n"
    )
```

Replace with:

```python
    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "MCP tools from Obot are exposed to the agent as mcp__obot__*.\n"
        "Slash commands: /quit, /exit.\n"
    )
```

- [ ] **Step 5: Confirm the file parses and imports resolve**

Run:
```bash
uv run python -c "import ast; ast.parse(open('samples/mcp_obot_linear_repl.py').read()); print('parse ok')"
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cr', 'samples/mcp_obot_linear_repl.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('import ok')
"
```
Expected: both print `... ok`.

- [ ] **Step 6: Commit**

```bash
git add samples/mcp_obot_linear_repl.py
git commit -m "Wire MCPConfig pointing at Obot's streamable-http endpoint"
```

---

## Task 3: Add `/status` and `/connect` slash commands

**Goal:** Two diagnostic slash commands. `/status` prints the tools the agent has loaded through Obot (which reveals whether Linear is reachable and which tool names are exposed). `/connect <connector>` prints the deep-link URL into Obot's user-settings page for that connector — the same URL the OAuth-banner callback (next task) prints automatically on failure.

**Files:**
- Modify: `samples/mcp_obot_linear_repl.py`

- [ ] **Step 1: Add the helper functions above `main`**

In `samples/mcp_obot_linear_repl.py`, insert these two functions immediately before `def main()`:

```python
def print_status(conversation: Conversation, obot_url: str) -> None:
    """Print the tools the agent has loaded from Obot.

    Empty if the agent hasn't initialized yet — send a message first and
    /status will populate. Reaching past tools_map into Obot's REST surface
    (catalog list, per-user connection state) is a follow-up once the exact
    REST paths are confirmed against a running Obot.
    """
    try:
        conversation._ensure_agent_ready()  # noqa: SLF001 — playground sample
    except Exception as exc:  # noqa: BLE001
        print(f"(could not initialize agent: {exc})")
        return

    tools = sorted(conversation.agent.tools_map.keys())
    print(f"\nObot URL: {obot_url}")
    print(f"Tools exposed to agent ({len(tools)}):")
    if not tools:
        print("  (none — Obot reachable? Is Linear authorized in the admin UI?)")
    else:
        for name in tools:
            print(f"  - {name}")
    print()


def print_connect_url(obot_url: str, connector: str) -> None:
    """Print the deep-link URL into Obot's user-settings page for a connector."""
    url = f"{obot_url}/user-settings/connectors/{connector}"
    print(f"\nReconnect at: {url}\n")
```

- [ ] **Step 2: Wire the slash commands into the REPL loop**

In `samples/mcp_obot_linear_repl.py`, find:

```python
        if line in ("/quit", "/exit"):
            break
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()
```

Replace with:

```python
        if line in ("/quit", "/exit"):
            break
        if line == "/status":
            print_status(conversation, obot_url)
            continue
        if line.startswith("/connect "):
            connector = line.split(" ", 1)[1].strip()
            if connector:
                print_connect_url(obot_url, connector)
            else:
                print("(usage: /connect <connector-name>, e.g. /connect linear)")
            continue
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()
```

- [ ] **Step 3: Update the banner to document the new slash commands**

In `samples/mcp_obot_linear_repl.py`, find:

```python
    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "MCP tools from Obot are exposed to the agent as mcp__obot__*.\n"
        "Slash commands: /quit, /exit.\n"
    )
```

Replace with:

```python
    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "MCP tools from Obot are exposed to the agent as mcp__obot__*.\n"
        "Slash commands: /status, /connect <connector>, /quit, /exit.\n"
    )
```

- [ ] **Step 4: Confirm the file parses and imports resolve**

Run:
```bash
uv run python -c "import ast; ast.parse(open('samples/mcp_obot_linear_repl.py').read()); print('parse ok')"
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cr', 'samples/mcp_obot_linear_repl.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('import ok')
"
```
Expected: both print `... ok`.

- [ ] **Step 5: Commit**

```bash
git add samples/mcp_obot_linear_repl.py
git commit -m "Add /status (prints agent tools) and /connect (prints reconnect URL) slash commands"
```

---

## Task 4: OAuth banner callback on tool failures

**Goal:** When the agent's tool call against Linear returns an OAuth-class error (token expired, revoked, consent re-required), print a prominent banner pointing at the Obot user-settings reconnect URL. The user reconnects out of band in Obot's UI and retypes the request — matching the production UX described in lock-in #5.

**Files:**
- Modify: `samples/mcp_obot_linear_repl.py`

- [ ] **Step 1: Add the `re` import**

In `samples/mcp_obot_linear_repl.py`, find:

```python
import os
import signal
import tempfile
```

Replace with:

```python
import os
import re
import signal
import tempfile
```

- [ ] **Step 2: Add the callback factory and OAuth pattern above `main`**

In `samples/mcp_obot_linear_repl.py`, insert this immediately before `def print_status(...)`:

```python
_OAUTH_PATTERN = re.compile(
    r"\b(401|Unauthorized|OAuth|token\s+expired|consent\s+required|invalid_token)\b",
    re.IGNORECASE,
)


def make_oauth_banner_callback(obot_url: str):
    """Build a Conversation callback that prints an OAuth reconnect banner
    when a tool observation contains an OAuth-class failure string.

    The agent still sees the same observation in its history and may surface
    the failure in its own response — the banner exists so the user sees the
    specific reconnect URL regardless of how the agent phrases the error.
    """
    def cb(event) -> None:
        text = str(event)
        if not _OAUTH_PATTERN.search(text):
            return
        url = f"{obot_url}/user-settings/connectors/linear"
        print()
        print("─" * 60)
        print("⚠  Linear OAuth needs attention for this user.")
        print(f"Reconnect at: {url}")
        print("After reconnecting, type your request again in this REPL.")
        print("─" * 60)
        print()

    return cb
```

- [ ] **Step 3: Pass the callback to the Conversation**

In `samples/mcp_obot_linear_repl.py`, find:

```python
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(prefix="obot_demo_"),
    )
    conversation.set_confirmation_policy(NeverConfirm())
```

Replace with:

```python
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(prefix="obot_demo_"),
        callbacks=[make_oauth_banner_callback(obot_url)],
    )
    conversation.set_confirmation_policy(NeverConfirm())
```

- [ ] **Step 4: Confirm the file parses and imports resolve**

Run:
```bash
uv run python -c "import ast; ast.parse(open('samples/mcp_obot_linear_repl.py').read()); print('parse ok')"
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cr', 'samples/mcp_obot_linear_repl.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('import ok')
"
```
Expected: both print `... ok`.

- [ ] **Step 5: Sanity-check the OAuth pattern regex**

Run:
```bash
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cr', 'samples/mcp_obot_linear_repl.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
p = m._OAUTH_PATTERN
print('hits:')
for s in ['HTTP 401 Unauthorized', 'token expired', 'consent required', 'invalid_token']:
    assert p.search(s), s
    print(f'  {s!r}: OK')
print('misses:')
for s in ['everything fine', 'connection refused', 'tool not found']:
    assert not p.search(s), s
    print(f'  {s!r}: OK')
"
```
Expected: prints `hits:` and `misses:` blocks with all assertions passing.

- [ ] **Step 6: Commit**

```bash
git add samples/mcp_obot_linear_repl.py
git commit -m "Add OAuth banner callback: prints reconnect URL on token failures"
```

---

## Task 5: Final manual verification

**Goal:** Walk the complete spec testing checklist end-to-end against a running Obot to confirm the sample matches the design.

**Files:** none changed; verification only.

**Prerequisite:** The user has completed the out-of-band setup from the spec's "Out-of-band setup the user does once before running" section — Obot running, Linear authorized, `OBOT_API_KEY` populated in `.env`.

- [ ] **Step 1: Confirm spec compliance**

Re-read the **Testing** section of `docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md`. Run `uv run python samples/mcp_obot_linear_repl.py` and walk each numbered item:

1. **Sample starts and prompts.** Banner prints, `> ` prompt appears.
2. **`/status` prints connected MCPs and available tools.** Should list one or more `mcp__obot__*` entries when Linear is authorized. If empty, the agent hasn't initialized yet — send a message first, then retry `/status`.
3. **`/connect linear` prints the reconnect URL.** Should print `http://localhost:8080/user-settings/connectors/linear`.
4. **A real Linear instruction works.** Type:
   ```
   Create a Linear issue in my Inbox titled "test from mcp-obot-linear-repl"
   ```
   The agent should call a Linear tool through Obot and report success. The issue should appear in your Linear account.
5. **OAuth recovery path.** With the agent running and Linear working, disconnect Linear inside Obot's UI (or revoke the grant in Linear's connected-apps page). Retype the instruction from step 4. Expected: the tool call fails, the OAuth reconnect banner prints, the REPL stays alive. Reconnect Linear in Obot's UI, retype the instruction, confirm it succeeds.
6. **Clean exits.** `Ctrl-C` at the prompt exits cleanly; `/quit` and `/exit` exit cleanly.

- [ ] **Step 2: If any check failed, fix and re-verify**

If a regression turns up, fix it as a focused diff (no scope creep), re-run the failing check, then commit:

```bash
git add samples/mcp_obot_linear_repl.py
git commit -m "<short fix message>"
```

- [ ] **Step 3: Confirm clean repo state**

Run:
```bash
git status
ls samples/
```
Expected: working tree clean. `samples/` contains `mcp_obot_linear_repl.py` alongside the existing samples (`recap.py`, `critic_refinement.py`, `set_confirmation_policy.py`).

---

## Self-review notes

- **Spec coverage:** Every in-scope spec section maps to a task. Architecture/Components 1-3 (LLM/Agent/Conversation, OBOT_URL/OBOT_API_KEY constants, MCPConfig) → Tasks 1-2. Components 4-6 (obot_status, REPL loop, OAuth banner callback) → Tasks 3-4. Out-of-band setup is the user's responsibility per spec; documented inline. Testing → Task 5. Non-goals (multi-user OAuth, per-intent filtering, OAuth driving from sample, dispatcher, OTel cross-boundary propagation per lock-in #9) — explicitly not implemented, by design.
- **Type/name consistency:** `obot_url`, `obot_api_key`, `mcp_config`, `print_status`, `print_connect_url`, `make_oauth_banner_callback`, `_OAUTH_PATTERN` are spelled identically across all tasks.
- **No placeholders:** every code step shows the actual code; every run step shows the actual command and expected output. The one "TBD" the spec called out (`/mcp` exact path on Obot's HTTP surface) is documented in Task 2 Step 3 as the working assumption with a note about the single line to change on first run, not as a TODO in the code.
- **Lock-in alignment:** The plan implements the experiment-side of every lock-in in spec section "Design lock-ins for Studio production" (lock-ins #1-8) that has a Python-code implication. PG setup, encryption-config file generation, audit-shim, catalog repo override — those are out-of-band per the spec.
