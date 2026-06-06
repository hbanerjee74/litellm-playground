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
            "OBOT_API_KEY is not set. Generate an API key in Obot's admin UI "
            "(http://localhost:8080) and add it to .env. See "
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
        "Slash commands: /quit, /exit.\n"
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
