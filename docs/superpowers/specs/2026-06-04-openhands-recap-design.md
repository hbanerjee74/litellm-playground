# OpenHands SDK `/recap` Sample — Design

**Date:** 2026-06-04
**Status:** Draft, pending implementation
**Scope:** Add a single self-contained OpenHands SDK sample that demonstrates a Claude Code `/recap`-style briefing card in a local REPL.

## Goal

Build the OpenHands-SDK equivalent of Claude Code's `/recap`: a non-mutating briefing card that summarizes the current session into four labeled sections — *What you did, What changed, What's next, Files touched*. The recap must not alter conversation state (matching Claude Code's contract that `/recap` does not modify history, unlike `/compact`).

## Non-goals

- No idle-timer auto-trigger. `FinishAction` is the trigger. A real idle timer would require threading and an interactive terminal contract that adds noise without clarifying the recap concept.
- No cross-session persistence. The recap is in-memory, printed once.
- No JSON-structured output. The LLM returns markdown; the script prints it verbatim inside a card frame.
- No `/compact` (`Conversation.condense()`) integration. That is a separate feature and a candidate for a future sample.
- No security analyzer or confirmation policy beyond `NeverConfirm()`. The existing `set_confirmation_policy.py` already covers that surface.

## Key SDK primitive: `Conversation.ask_agent`

`Conversation.ask_agent(question: str) -> str` is the load-bearing primitive. From the SDK docstring:

> Ask the agent a simple, stateless question and get a direct LLM response. This bypasses the normal conversation flow and does **not** modify, persist, or become part of the conversation state. The request is not remembered by the main agent, no events are recorded, and execution status is untouched. It is also thread-safe and may be called while `conversation.run()` is executing in another thread.

Internally `ask_agent` calls `prepare_llm_messages(self.state.events, additional_messages=[user_message])`, so the full event history (including all `FileEditorTool` and `TerminalTool` tool calls) is sent to the LLM. We do not need to scan the event log ourselves — the LLM sees it.

This is the ideal substrate for `/recap` because it satisfies the non-mutation contract by construction.

## Architecture

A single file: `samples/recap.py`. Runnable directly via `uv run python samples/recap.py`.

### Components

1. **LLM + Agent + Conversation setup.** Same pattern as `samples/set_confirmation_policy.py`: load `.env`, build an `LLM` for Minimax via OpenRouter, register `TerminalTool` and `FileEditorTool`, construct `Conversation(agent=agent, workspace=os.getcwd())`. Set `NeverConfirm()` policy. No security analyzer.

2. **`RECAP_PROMPT` constant.** A fixed prompt string asking for the four labeled sections, with explicit instructions to use file paths and commands verbatim from history and to write `(nothing)` for empty sections.

3. **`print_recap(conversation)` function.** Calls `conversation.ask_agent(RECAP_PROMPT)`, wraps the result in a Unicode box-drawing card frame with a `Recap` header, prints to stdout. Catches any exception from `ask_agent` and prints `(recap failed: <error>)` instead of propagating.

4. **`last_action_is_finish(conversation)` helper.** Walks `conversation.state.events` in reverse and returns `True` if the most recent action-bearing event holds a `FinishAction`. Used as the auto-trigger predicate after each `conversation.run()` returns.

5. **REPL loop.** Reads stdin one line at a time. Dispatches:
   - `/recap` → `print_recap(conversation)`, re-prompt. No agent activity.
   - `/quit` or EOF or `Ctrl-C` at the prompt → exit cleanly.
   - Anything else → `conversation.send_message(line)`, `conversation.run()`. Then if `last_action_is_finish(conversation)` is true, auto-call `print_recap(conversation)`.

### Data flow

```
user line ──> REPL dispatcher
              │
              ├── "/recap" ─────────────────────────────────> print_recap ──> ask_agent ──> stdout
              ├── "/quit" / EOF ─> exit
              └── other text ─> send_message ─> run() ──> [tail event] ─> if FinishAction ─> print_recap
```

### File layout impact

- New file: `samples/recap.py`.
- No changes to `pyproject.toml` — `openhands-sdk`, `openhands-tools`, `python-dotenv` are already present.
- No changes to `.env.example` — `OPENROUTER_API_KEY` is already declared.
- No new modules under `samples/` beyond `recap.py`. `print_recap` and helpers live top-of-file.

## Error handling

| Condition | Behavior |
|---|---|
| `KeyboardInterrupt` at REPL input prompt | Clean exit, no traceback. Reuse the `signal.signal(SIGINT, ...)` pattern from `set_confirmation_policy.py`. |
| `KeyboardInterrupt` mid-`conversation.run()` | Break the REPL loop, call `print_recap` once on the way out, then exit. |
| `ask_agent` raises inside `print_recap` | Catch, print `(recap failed: <error>)` inside the card frame. Continue the REPL. |
| `/recap` typed before any user message | Let `ask_agent` answer normally — the LLM will note an empty session. No special case. |
| `run()` returns without a `FinishAction` (e.g., agent paused) | No auto-recap. User can still type `/recap` manually. |

## Recap prompt

```
Produce a concise session recap with EXACTLY these four sections,
each as a bulleted list:

## What you did
## What changed
## What's next
## Files touched

Be specific. Use file paths and commands verbatim where they appear in the
history. If a section has nothing to report, write "(nothing)" under it.
Do not add any preamble or closing remarks outside the four sections.
```

## Demo session (expected behavior)

```
> List the files in this directory.
... agent runs TerminalTool ...
┌─ Recap ────────────────────────────────────────────────────
│ ## What you did
│ - Listed files in the current working directory.
│ ## What changed
│ - (nothing)
│ ## What's next
│ - Awaiting next instruction.
│ ## Files touched
│ - (nothing)
└────────────────────────────────────────────────────────────

> Create a file recap_demo.txt with the line "hello recap".
... agent runs FileEditorTool ...
┌─ Recap ────────────────────────────────────────────────────
│ ## What you did
│ - Created recap_demo.txt with one line of content.
│ ## What changed
│ - New file recap_demo.txt added to the workspace.
│ ## What's next
│ - Confirm contents or extend the file further.
│ ## Files touched
│ - recap_demo.txt
└────────────────────────────────────────────────────────────

> /recap
(same card, regenerated from current state, no new events)

> /quit
```

## Testing

This is a playground sample. Verification is manual:

1. `uv run python samples/recap.py` starts and prompts.
2. Issuing a real instruction produces a `FinishAction` and an auto-recap card.
3. `/recap` on demand produces a card without sending a message to the agent.
4. `conversation.state.events` length is unchanged across `print_recap` calls (the non-mutation contract). Spot-check by adding a temporary `print(len(conversation.state.events))` during dev.
5. `Ctrl-C` at the prompt exits cleanly; `Ctrl-C` mid-run prints a farewell recap and exits.

No automated tests. The playground does not have a test harness, and adding one for one sample is out of scope.

## Open questions

None. Architecture, primitive, trigger, and prompt are all settled.

## Future extensions (not in scope)

- Idle-timer auto-trigger via a background `threading.Timer` reset on every input line.
- `/compact` sample using `Conversation.condense()`.
- Cross-session resume: persist the last recap to disk and inject it as the opening assistant message in a fresh conversation.
- Plug the recap into a non-REPL agent loop (e.g., after each high-level task in a longer plan).
