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


def print_recap(conversation: Conversation) -> None:
    """Print a non-mutating recap card built from the current conversation.

    Safe to call from a background thread — Conversation.ask_agent is
    documented thread-safe and does not modify, persist, or become part of
    the conversation state.
    """
    try:
        text = conversation.ask_agent(RECAP_PROMPT)
    except Exception as exc:  # noqa: BLE001 — deliberate: never let recap kill the REPL
        text = f"(recap failed: {exc})"

    print("\n┌─ Recap " + "─" * 60)
    for line in text.splitlines() or [""]:
        print("│ " + line)
    print("└" + "─" * 68 + "\n")


_idle_timer: threading.Timer | None = None


def _auto_recap(conversation: Conversation) -> None:
    """Timer callback: print the recap, then redraw the REPL prompt.

    The main thread is blocked in input("> "), so its prompt was drawn
    before the card. We redraw "> " after the card so the user sees a
    fresh prompt instead of a bare cursor.
    """
    print_recap(conversation)
    print("> ", end="", flush=True)


def arm_idle_timer(conversation: Conversation) -> None:
    """Replace any pending timer with a fresh IDLE_SECONDS timer.

    Called every time conversation.run() returns. The timer fires in a
    background thread and calls print_recap if not canceled first.
    """
    global _idle_timer
    cancel_idle_timer()
    _idle_timer = threading.Timer(
        IDLE_SECONDS, lambda: _auto_recap(conversation)
    )
    _idle_timer.daemon = True
    _idle_timer.start()


def cancel_idle_timer() -> None:
    """Cancel any pending idle timer. No-op if none is armed."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


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

        cancel_idle_timer()  # any pending recap is preempted by the next submit

        if line == "/quit":
            break
        if line == "/recap":
            print_recap(conversation)
            continue
        if not line:
            continue

        conversation.send_message(line)
        conversation.run()
        arm_idle_timer(conversation)

    cancel_idle_timer()


if __name__ == "__main__":
    main()
