"""Custom critic + interactive REPL demonstrating iterative refinement.

Each user message triggers the main agent. When the agent calls FinishAction,
the IntentCritic spins up a *fresh* judge Conversation (same Minimax model,
new LLM instance, read-only TerminalTool only) that independently verifies
the agent's work and returns a score 0.0-1.0. If the score is below the
configured threshold, the SDK's CriticMixin re-prompts the main agent with
get_followup_prompt and the loop continues, up to max_iterations.
"""

from __future__ import annotations

import os
import re
import signal
import tempfile
from collections.abc import Sequence
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from pydantic import Field, SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.critic.base import CriticBase, IterativeRefinementConfig
from openhands.sdk.critic.result import CriticResult
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins.finish import FinishAction

from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool


if TYPE_CHECKING:
    from openhands.sdk.event.base import Event, LLMConvertibleEvent


JUDGE_PROMPT_TEMPLATE = """\
You are a senior Python code reviewer. Evaluate the agent's work against the
user's request and the rubric below.

USER'S ORIGINAL REQUEST:
{user_intent}

AGENT'S CLAIMED ACTIONS:
{action_summary}

Independently verify the work — do NOT trust the claimed actions summary.
Use only read-only terminal commands (ls, cat, head, tail, grep, find, wc,
diff) to inspect the workspace. Do NOT modify it (no rm, mv, cp, touch,
redirects, sed -i, or any command that writes).

Scoring rubric — score each category from 0.0 (failing) to 1.0 (perfect).
Skip any category that does not apply to the user's request rather than
penalizing it. Be fair: do not dock points for missing production polish
the user did not ask for, and do not inflate scores out of politeness.

1. Correctness & Logic (weight 25%)
   - Does the code do what the user asked? Stated edge cases handled?
   - Verify: cat the files the agent claims to have created; grep for the
     specific behaviors the user requested.

2. Security & Robustness (weight 25%)
   - Obvious vulnerabilities (eval on untrusted input, hardcoded secrets,
     SQL injection, unsafe deserialization)?
   - Error handling appropriate (specific exceptions, no bare `except: pass`)?
   - Verify: grep for eval(, exec(, `except:`, pickle.loads, hardcoded
     credentials.

3. Performance & Efficiency (weight 20%)
   - Reasonable algorithmic complexity for the inputs implied by the request?
   - Idiomatic use of sets/dicts for lookups; no redundant loops or I/O?
   - Verify: cat the implementation and reason about the hot path.

4. Pythonic Idioms & Best Practices (weight 15%)
   - PEP 8, modern features (f-strings, context managers, type hints) used
     correctly where they fit. Judge only if the user implied
     production-quality code.
   - Verify: cat the files; check naming and structure.

5. Maintainability & Cleanliness (weight 15%)
   - Modular structure, useful docstrings, no dead code or unused imports.
   - Verify: cat each file and skim for clarity.

When you are confident, call FinishAction with a message whose FIRST LINE is
exactly:

  SCORE: <weighted average of the categories, between 0.0 and 1.0>

Followed by your reasoning on subsequent lines, including the per-category
scores so the agent can see what to fix. The agent will be asked to refine
its work whenever SCORE is below 0.7.

Example final line: SCORE: 0.62
"""


PYTHON_AGENT_SUFFIX = (
    "This REPL is dedicated to Python coding tasks. Treat every user "
    "request as a Python coding task: produce Python code that is correct, "
    "secure, reasonably efficient, idiomatic, and maintainable. When the "
    "user is implicit about style, default to PEP 8, type hints, and clear "
    "naming. Verify your work (e.g. by reading the files you created or "
    "running them) before calling FinishAction."
)


class IntentCritic(CriticBase):
    """Local LLM-as-judge critic that spins up a fresh Conversation per evaluate().

    The judge gets read-only tools (typically just TerminalTool) and is
    instructed to verify the main agent's claimed work against the user's
    original intent. Returns a score 0.0-1.0; the SDK's iterative_refinement
    machinery re-prompts the main agent whenever the score is below the
    threshold.
    """

    llm: LLM
    tools: list[Tool] = Field(default_factory=list)
    workspace: str = Field(
        ...,
        description="Workspace path the judge Conversation operates in. Must "
        "match the main agent's workspace so the judge sees the same files.",
    )

    def evaluate(
        self,
        events: Sequence["LLMConvertibleEvent"],
        git_patch: str | None = None,
    ) -> CriticResult:
        user_intent = _extract_user_intent(events) or "(no user message found)"
        action_summary = _summarize_actions(events) or "  (no actions recorded)"

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            user_intent=user_intent,
            action_summary=action_summary,
        )

        judge_llm = self.llm.model_copy(
            update={"usage_id": "judge-llm"}, deep=True
        )
        judge_agent = Agent(llm=judge_llm, tools=self.tools)
        judge_conv = Conversation(agent=judge_agent, workspace=self.workspace)
        judge_conv.set_confirmation_policy(NeverConfirm())

        judge_conv.send_message(prompt)
        judge_conv.run()

        verdict = _last_judge_verdict(judge_conv.state.events)
        if verdict is None:
            return CriticResult(
                score=0.0,
                message="judge produced no final response",
            )

        score = _parse_score(verdict)
        if score is None:
            preview = verdict.strip().splitlines()[0][:200] if verdict.strip() else ""
            return CriticResult(
                score=0.0,
                message=f"unparseable judge response (no SCORE: found; first line: {preview!r})",
            )
        return CriticResult(score=score, message=verdict.strip())

    def get_followup_prompt(
        self, critic_result: CriticResult, iteration: int
    ) -> str:
        return (
            f"A reviewer scored your work at {critic_result.score:.0%} "
            f"(iteration {iteration}).\n\n"
            f"Reviewer feedback:\n{critic_result.message}\n\n"
            "Address the gaps and call FinishAction again only when you "
            "have fully completed the original request."
        )


def _extract_user_intent(events: Sequence["LLMConvertibleEvent"]) -> str:
    parts: list[str] = []
    for ev in events:
        if isinstance(ev, MessageEvent) and ev.source == "user":
            for content in ev.llm_message.content:
                if isinstance(content, TextContent):
                    parts.append(content.text)
    return "\n".join(p.strip() for p in parts if p.strip())


def _summarize_actions(events: Sequence["LLMConvertibleEvent"]) -> str:
    lines: list[str] = []
    for ev in events:
        if isinstance(ev, ActionEvent) and ev.action is not None:
            name = type(ev.action).__name__
            summary = str(ev.action)[:300].replace("\n", " ")
            lines.append(f"  - {name}: {summary}")
    return "\n".join(lines)


_SCORE_PATTERN = re.compile(r"\bSCORE\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _last_judge_verdict(events: Sequence) -> str | None:
    """Find the judge's final verdict text.

    Walks events in reverse and returns whichever comes first:
    - The message of the most recent FinishAction, or
    - The text of the most recent agent MessageEvent (fallback for models
      that emit a plain reply instead of calling FinishAction).
    """
    for ev in reversed(list(events)):
        if isinstance(ev, ActionEvent) and isinstance(ev.action, FinishAction):
            return ev.action.message
        if isinstance(ev, MessageEvent) and ev.source == "agent":
            parts = [
                c.text
                for c in ev.llm_message.content
                if isinstance(c, TextContent)
            ]
            text = "\n".join(parts).strip()
            if text:
                return text
    return None


def _parse_score(text: str) -> float | None:
    """Extract a 0.0-1.0 score from anywhere in the text via SCORE: pattern."""
    match = _SCORE_PATTERN.search(text)
    if match is None:
        return None
    try:
        return max(0.0, min(1.0, float(match.group(1))))
    except ValueError:
        return None


def print_critic_event(event: "Event") -> None:
    """Conversation callback: print critic results inline as they arrive."""
    critic_result = getattr(event, "critic_result", None)
    if critic_result is None:
        return
    msg = critic_result.message or ""
    first_line = msg.splitlines()[0] if msg else ""
    print(
        f"\n[critic] score={critic_result.score:.2f} — {first_line}\n",
        flush=True,
    )


def build_conversation() -> Conversation:
    load_dotenv()
    llm = LLM(
        model="openrouter/z-ai/glm-5.1",
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url="https://openrouter.ai/api/v1",
    )

    main_tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ]
    judge_tools = [Tool(name=TerminalTool.name),Tool(name=TaskTrackerTool.name)]

    workspace = tempfile.mkdtemp(prefix="critic_demo_")
    print(f"📁 Workspace: {workspace}\n", flush=True)

    critic = IntentCritic(
        llm=llm,
        tools=judge_tools,
        workspace=workspace,
        iterative_refinement=IterativeRefinementConfig(
            success_threshold=0.7,
            max_iterations=3,
        ),
    )

    agent = Agent(
        llm=llm,
        tools=main_tools,
        critic=critic,
        agent_context=AgentContext(system_message_suffix=PYTHON_AGENT_SUFFIX),
    )
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[print_critic_event],
    )
    conversation.set_confirmation_policy(NeverConfirm())
    return conversation


def main() -> None:
    signal.signal(
        signal.SIGINT,
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    conversation = build_conversation()
    print(
        "OpenHands Python coding REPL with critic refinement.\n"
        "Send a Python coding task. When the agent calls FinishAction,\n"
        "a fresh judge Conversation (read-only) scores the work 0.0-1.0\n"
        "across correctness, security, performance, idioms, and maintainability.\n"
        "Below 0.7 → the agent gets feedback and tries again (up to 3 times).\n"
        "Type /quit or /exit to leave.\n"
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
