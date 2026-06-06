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
