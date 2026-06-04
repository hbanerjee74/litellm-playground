# OpenHands `/recap` Sample — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `samples/recap.py` — a single self-contained OpenHands SDK REPL that prints a Claude Code `/recap`-style briefing card on demand (`/recap`) and automatically 5 seconds after each `conversation.run()` completes.

**Architecture:** Plain blocking `input()` REPL on the main thread. After every `conversation.run()`, arm a `threading.Timer(5.0, ...)` that calls `print_recap(conversation)` from a background thread; cancel that timer the instant the user submits the next line. `print_recap` calls the SDK's non-mutating `Conversation.ask_agent` primitive (thread-safe, full event history, no state change).

**Tech Stack:** Python ≥ 3.12, `openhands-sdk`, `openhands-tools`, `python-dotenv`, Minimax via OpenRouter (same setup as `samples/set_confirmation_policy.py`). `uv` for dependency / run management.

**Spec:** `docs/superpowers/specs/2026-06-04-openhands-recap-design.md`

**Note on testing:** Per the spec, this is a playground sample with no test harness — there are no automated tests. Each task ends with a manual `uv run` verification step plus a commit.

---

## File Structure

- **Create:** `samples/recap.py` — entire sample lives here (constants, helpers, REPL, `main`). Single file matches the existing one-sample-one-file convention from `samples/set_confirmation_policy.py`.
- **No other files change.** `pyproject.toml` already declares `openhands-sdk`, `openhands-tools`, and `python-dotenv`. `.env.example` already declares `OPENROUTER_API_KEY`.

---

## Task 1: Skeleton — REPL + LLM/Agent/Conversation setup, no recap yet

**Goal:** A working interactive REPL where the user can talk to the agent (Minimax via OpenRouter, terminal + file editor tools). No `/recap`, no idle timer yet. Confirms the agent loop works before adding recap behavior.

**Files:**
- Create: `samples/recap.py`

- [ ] **Step 1: Verify dependencies and env var are already in place**

Run:
```bash
grep -E "openhands-sdk|openhands-tools|python-dotenv" pyproject.toml
grep OPENROUTER_API_KEY .env.example
```
Expected: each grep prints a matching line. If not, stop — the spec assumes these are present.

- [ ] **Step 2: Create `samples/recap.py` with the skeleton**

Write this exact content:

```python
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
```

- [ ] **Step 3: Confirm the file parses and imports resolve**

Run:
```bash
uv run python -c "import ast; ast.parse(open('samples/recap.py').read()); print('parse ok')"
uv run python -c "import samples.recap; print('import ok')"
```
Expected: both print `... ok`. If the import fails because `samples/` is not a package, that is fine — fall back to:
```bash
uv run python -c "import importlib.util, pathlib; spec = importlib.util.spec_from_file_location('recap', 'samples/recap.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('import ok')"
```

- [ ] **Step 4: Manual smoke test of the skeleton REPL**

Run:
```bash
uv run python samples/recap.py
```
Expected: prints the banner and `> ` prompt. Then:
- Type `hello, list the files in this directory` and hit enter. The agent should run `TerminalTool` and print results. The prompt returns.
- Type `/recap`. Expected output: `(recap not implemented yet)`. Prompt returns.
- Type `/quit`. The process exits cleanly with no traceback.
- Re-run and press Ctrl-C at the prompt. The process exits cleanly with no traceback.

If any step misbehaves, fix before continuing.

- [ ] **Step 5: Commit**

```bash
git add samples/recap.py
git commit -m "Add OpenHands /recap sample skeleton: REPL with LLM, agent, tools"
```

---

## Task 2: Implement `print_recap` and wire it into `/recap`

**Goal:** The `/recap` command produces a real briefing card by calling `Conversation.ask_agent` with `RECAP_PROMPT` and printing the result inside a Unicode box-drawing frame. No idle timer yet.

**Files:**
- Modify: `samples/recap.py`

- [ ] **Step 1: Add the `print_recap` function above `main`**

Insert this function in `samples/recap.py` immediately after the `build_conversation` function (and before `main`):

```python
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
```

- [ ] **Step 2: Replace the `/recap` placeholder in `main`**

In `samples/recap.py`, find:

```python
        if line == "/recap":
            print("(recap not implemented yet)")
            continue
```

Replace with:

```python
        if line == "/recap":
            print_recap(conversation)
            continue
```

- [ ] **Step 3: Manual verification — `/recap` produces a real card**

Run:
```bash
uv run python samples/recap.py
```

Then:
1. Type `list the files in this directory`. Let the agent finish.
2. Type `/recap`. Expected: a box-drawn card with `┌─ Recap ...` at top, `└...` at bottom, and four `## ` sections (`What you did`, `What changed`, `What's next`, `Files touched`), each as bullets. Empty sections show `(nothing)`.
3. Type `/quit`. Process exits cleanly.

If the LLM formatting drifts (e.g., extra preamble), confirm it's the model's doing — the prompt is fixed and section headers are explicit. Acceptable as long as the four sections are present.

- [ ] **Step 4: Verify the non-mutation contract**

Temporarily replace the entire `print_recap` body with this debug variant:

```python
def print_recap(conversation: Conversation) -> None:
    before = len(conversation.state.events)
    try:
        text = conversation.ask_agent(RECAP_PROMPT)
    except Exception as exc:  # noqa: BLE001
        text = f"(recap failed: {exc})"
    after = len(conversation.state.events)
    print(f"[debug] events before={before} after={after}")

    print("\n┌─ Recap " + "─" * 60)
    for line in text.splitlines() or [""]:
        print("│ " + line)
    print("└" + "─" * 68 + "\n")
```

Run the REPL, type a message, let it finish, then type `/recap`. Expected: `[debug] events before=N after=N` (same number). Confirms `ask_agent` does not append to `state.events`.

- [ ] **Step 5: Revert the debug version of `print_recap`**

Restore `print_recap` to the exact version from Step 1 (delete the `before`, `after`, and `print(f"[debug] ...")` lines so only the production body remains).

- [ ] **Step 6: Commit**

```bash
git add samples/recap.py
git commit -m "Add print_recap and wire /recap command via ask_agent"
```

---

## Task 3: Idle timer — arm after `run()`, cancel on next submit

**Goal:** Auto-print a recap card 5 seconds after each `conversation.run()` returns, unless the user submits another line first.

**Files:**
- Modify: `samples/recap.py`

- [ ] **Step 1: Add timer state and helpers above `main`**

In `samples/recap.py`, insert these immediately after the `print_recap` function and before `main`:

```python
_idle_timer: threading.Timer | None = None


def arm_idle_timer(conversation: Conversation) -> None:
    """Replace any pending timer with a fresh IDLE_SECONDS timer.

    Called every time conversation.run() returns. The timer fires in a
    background thread and calls print_recap if not canceled first.
    """
    global _idle_timer
    cancel_idle_timer()
    _idle_timer = threading.Timer(
        IDLE_SECONDS, lambda: print_recap(conversation)
    )
    _idle_timer.daemon = True
    _idle_timer.start()


def cancel_idle_timer() -> None:
    """Cancel any pending idle timer. No-op if none is armed."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None
```

- [ ] **Step 2: Wire the timer into `main`**

In `samples/recap.py`, find the `main` loop body and update it so:
- Every iteration cancels the timer right after reading a line (before processing it).
- `arm_idle_timer` is called immediately after `conversation.run()` returns.
- `cancel_idle_timer()` runs once on the way out of the loop.

Replace the entire `while True:` block inside `main` with:

```python
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
```

- [ ] **Step 3: Manual verification — auto-recap fires after idle**

Run:
```bash
uv run python samples/recap.py
```

Verify each row of the spec's testing checklist:

1. Type `list the files in this directory`. Let the agent finish. Wait ~5s without typing. Expected: a recap card appears automatically. (It will print after the dangling `> ` — visual is imperfect but the card content is correct.)
2. Wait another 10s without typing. Expected: no second recap. (Timer was one-shot and isn't re-armed until the next `run()`.)
3. Type `create a file recap_demo.txt with the line "hello recap"` and hit enter. Let the agent finish. Wait 5s. Expected: a fresh recap card with `recap_demo.txt` listed under `Files touched`.
4. Type `list the files again`. **Within 5s**, type `/recap` and hit enter. Expected: only one card prints (the manual one), confirming the auto-timer was canceled before it could fire.
5. Type `/quit`. Process exits cleanly. Re-run and press Ctrl-C at the prompt: clean exit, no traceback, no zombie timer threads (process actually exits because the timer is `daemon=True`).

If any check fails, debug before committing. Common failure: forgetting `daemon=True` causes the process to hang at exit waiting for the timer.

- [ ] **Step 4: Clean up the demo file**

```bash
rm -f recap_demo.txt
```

- [ ] **Step 5: Commit**

```bash
git add samples/recap.py
git commit -m "Add 5s idle timer that auto-prints recap after conversation.run()"
```

---

## Task 4: Final manual verification per the spec

**Goal:** Walk the complete spec testing checklist end-to-end to confirm the sample matches the design.

**Files:** none changed; verification only.

- [ ] **Step 1: Confirm spec compliance**

Open `docs/superpowers/specs/2026-06-04-openhands-recap-design.md` and re-read the **Testing** section. For each numbered item (1–8), run the scenario against `uv run python samples/recap.py` and confirm the behavior.

Specifically:

1. `uv run python samples/recap.py` starts and prompts. ✓ if banner + `> ` appears.
2. Agent finishes → 5s of silence → recap card prints. ✓ if card appears.
3. 5s more silence → no second recap. ✓ if nothing prints.
4. Submit a line within 5s → timer canceled, no auto-recap. ✓ if no spurious card.
5. Next `run()` finishes → 5s silence → fresh card. ✓.
6. `/recap` on demand → card prints, no agent activity (you should NOT see the agent invoke any tool). ✓.
7. Non-mutation: optional spot-check — temporarily add `print(len(conversation.state.events))` before and after a `/recap` call; confirm equal. (Skip if Task 2 Step 4 already convinced you.)
8. Ctrl-C at the prompt exits cleanly; the process actually terminates (no hang on the daemon timer).

- [ ] **Step 2: If any check failed, fix and re-verify**

If a regression turns up, fix it as a focused diff (no scope creep) and re-run the failing check. Then commit:

```bash
git add samples/recap.py
git commit -m "<short fix message>"
```

- [ ] **Step 3: Confirm clean repo state**

Run:
```bash
git status
ls samples/
```
Expected: working tree clean (no leftover `recap_demo.txt`, no debug prints). `samples/` contains `recap.py` and `set_confirmation_policy.py`.

---

## Self-review notes

- **Spec coverage:** Every spec section maps to a task. Architecture/Components → Tasks 1–3. Recap prompt → Task 1 (constant). Error handling (try/except in `print_recap`, `daemon=True` timer, EOF/KeyboardInterrupt at prompt, cancel-on-exit) → Tasks 2 and 3. Demo session / Testing → Task 4. Non-goals (no `FinishAction` trigger, no JSON, no `/compact`, no persistence) → satisfied by not adding them.
- **Type/name consistency:** `IDLE_SECONDS`, `RECAP_PROMPT`, `print_recap`, `arm_idle_timer`, `cancel_idle_timer`, `_idle_timer`, `build_conversation`, `main` are spelled identically across all tasks.
- **No placeholders:** every code step shows the actual code; every run step shows the actual command and expected behavior.
