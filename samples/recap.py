"""Claude Code /recap-style briefing card sample, built on the OpenHands SDK.

Runs an interactive REPL. After every conversation.run() returns, a 5-second
idle timer is armed in a background thread; if the user does not submit a new
line within 5 seconds, the timer calls print_recap, which uses the SDK's
non-mutating Conversation.ask_agent primitive to print a briefing card. The
user can also request a recap on demand with the /recap command.
"""

from __future__ import annotations

import os
import signal
import threading

from dotenv import load_dotenv
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.tool import Tool

from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool


IDLE_SECONDS = 5.0

RECAP_PROMPT = """\
Produce a concise session recap with EXACTLY these four sections,
each as a bulleted list:

## What you did
## What changed
## What's next
## Files touched

Be specific. Use file paths and commands verbatim where they appear in the
history. If a section has nothing to report, write "(nothing)" under it.
Do not add any preamble or closing remarks outside the four sections.
"""


def build_conversation() -> Conversation:
    load_dotenv()
    llm = LLM(
        model="minimax/minimax-m3",
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url="https://openrouter.ai/api/v1",
    )
    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]
    agent = Agent(llm=llm, tools=tools)
    conversation = Conversation(agent=agent, workspace=os.getcwd())
    conversation.set_confirmation_policy(NeverConfirm())
    return conversation


def main() -> None:
    # Clean ^C exit, matching set_confirmation_policy.py.
    signal.signal(
        signal.SIGINT,
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    conversation = build_conversation()
    print("OpenHands recap REPL. Type a message, /recap, or /quit.")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if line == "/quit":
            break
        if line == "/recap":
            print("(recap not implemented yet)")
            continue
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()


if __name__ == "__main__":
    main()
