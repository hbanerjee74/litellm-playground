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

from fastmcp.mcp_config import MCPConfig

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm


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

    llm = LLM(
        model="openrouter/z-ai/glm-5.1",
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url="https://openrouter.ai/api/v1",
    )
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
    conversation = Conversation(
        agent=agent,
        workspace=tempfile.mkdtemp(prefix="obot_demo_"),
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


if __name__ == "__main__":
    main()
