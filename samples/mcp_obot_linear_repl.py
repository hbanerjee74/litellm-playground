"""OpenHands + Obot MCP Gateway + Linear REPL.

Evaluation harness for the configure-connectors design landing in Studio.
See docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md for
the full design, lock-ins for Studio production, and the out-of-band Obot
setup steps (Obot must be running locally and Linear authorized through
Obot's admin UI before this sample is useful).
"""

from __future__ import annotations

import os
import re
import signal
import tempfile

from dotenv import load_dotenv
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm


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
        # Assumes Event's __str__ includes observation text. Verified at Task 5
        # manual run; if it turns out OpenHands' Event __str__ elides the
        # observation, swap to checking event.observation / event.content /
        # event.text or similar typed attribute.
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


def main() -> None:
    load_dotenv()

    # Clean ^C exit, matching set_confirmation_policy.py / recap.py.
    signal.signal(
        signal.SIGINT,
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    obot_url = os.environ.get("OBOT_URL", "http://localhost:8080")
    obot_api_key = os.environ.get("OBOT_API_KEY")
    if not obot_api_key:
        raise SystemExit(
            f"OBOT_API_KEY is not set. Generate an API key in Obot's admin UI "
            f"({obot_url}) and add it to .env. See "
            "docs/superpowers/specs/2026-06-06-mcp-obot-linear-repl-design.md "
            "for full out-of-band setup steps."
        )

    # Obot exposes each user-installed MCP server at /mcp-connect/<server_id>/mcp.
    # The server_id is per-install (auto-generated when you add the server in
    # Obot's UI). Discover it once via:
    #   curl -H "Authorization: Bearer $OBOT_BOOTSTRAP_TOKEN" \
    #     "$OBOT_URL/api/all-mcps/servers" | python3 -m json.tool
    # then add OBOT_MCP_SERVER_ID=<id> to .env. Defaults to the empty string,
    # which triggers a clear error rather than a confusing 405 from /mcp.
    obot_mcp_server_id = os.environ.get("OBOT_MCP_SERVER_ID", "")
    if not obot_mcp_server_id:
        raise SystemExit(
            "OBOT_MCP_SERVER_ID is not set. After adding Linear in Obot's "
            f"admin UI ({obot_url}), look up the auto-generated server ID via "
            "the docker label (`docker inspect <linear-container> "
            "--format '{{.Config.Labels}}'` → mcp.server.id) or via "
            "`curl -H \"Authorization: Bearer $OBOT_BOOTSTRAP_TOKEN\" "
            "$OBOT_URL/api/all-mcps/servers`. Add OBOT_MCP_SERVER_ID=<id> "
            "to .env. See spec for details."
        )

    llm = LLM(
        model="openrouter/z-ai/glm-5.1",
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url="https://openrouter.ai/api/v1",
    )
    # Agent accepts mcp_config as a raw dict in the fastmcp/MCPConfig schema;
    # passing a validated MCPConfig instance fails Agent's pydantic validator
    # in openhands-sdk 1.24.0 ("mcp_config must be a dictionary when provided").
    mcp_config = {
        "mcpServers": {
            "obot": {
                "transport": "streamable-http",
                "url": f"{obot_url}/mcp-connect/{obot_mcp_server_id}/mcp",
                "headers": {"Authorization": f"Bearer {obot_api_key}"},
            }
        }
    }

    agent = Agent(llm=llm, mcp_config=mcp_config)
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(prefix="obot_demo_"),
        callbacks=[make_oauth_banner_callback(obot_url)],
    )
    conversation.set_confirmation_policy(NeverConfirm())

    print(
        "OpenHands + Obot + Linear REPL.\n"
        f"Obot configured at: {obot_url}\n"
        "MCP tools from Obot are exposed to the agent as mcp__obot__*.\n"
        "Slash commands: /status, /connect <connector>, /quit, /exit.\n"
    )

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if line in ("/quit", "/exit"):
            break
        if line == "/status":
            print_status(conversation, obot_url)
            continue
        if line == "/connect" or line.startswith("/connect "):
            connector = line[len("/connect"):].strip()
            if connector:
                print_connect_url(obot_url, connector)
            else:
                print("(usage: /connect <connector-name>, e.g. /connect linear)")
            continue
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()


if __name__ == "__main__":
    main()
