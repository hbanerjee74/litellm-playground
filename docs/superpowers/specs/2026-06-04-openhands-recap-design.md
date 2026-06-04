# OpenHands SDK `/recap` Sample — Design

**Date:** 2026-06-04
**Status:** Draft, pending implementation
**Scope:** Add a single self-contained OpenHands SDK sample that demonstrates a Claude Code `/recap`-style briefing card in a local REPL.

## Goal

Build the OpenHands-SDK equivalent of Claude Code's `/recap`: a non-mutating briefing card that summarizes the current session into four labeled sections — *What you did, What changed, What's next, Files touched*. The recap must not alter conversation state (matching Claude Code's contract that `/recap` does not modify history, unlike `/compact`).

## Non-goals

- No `FinishAction`-based trigger. The auto-trigger is idle time at the prompt, matching Claude Code's "user stepped away" semantics.
- No cross-session persistence. The recap is in-memory, printed once.
- No JSON-structured output. The LLM returns markdown; the script prints it verbatim inside a card frame.
- No `/compact` (`Conversation.condense()`) integration. That is a separate feature and a candidate for a future sample.
- No security analyzer or confirmation policy beyond `NeverConfirm()`. The existing `set_confirmation_policy.py` already covers that surface.
- No special handling for the case where the recap card prints while the user is mid-typing. Terminal redraw cosmetics in canonical-line mode are out of scope; the kernel's line buffer preserves the user's keystrokes.

## Key SDK primitive: `Conversation.ask_agent`

`Conversation.ask_agent(question: str) -> str` is the load-bearing primitive. From the SDK docstring:

> Ask the agent a simple, stateless question and get a direct LLM response. This bypasses the normal conversation flow and does **not** modify, persist, or become part of the conversation state. The request is not remembered by the main agent, no events are recorded, and execution status is untouched. It is also thread-safe and may be called while `conversation.run()` is executing in another thread.

Internally `ask_agent` calls `prepare_llm_messages(self.state.events, additional_messages=[user_message])`, so the full event history (including all `FileEditorTool` and `TerminalTool` tool calls) is sent to the LLM. We do not need to scan the event log ourselves — the LLM sees it.

This is the ideal substrate for `/recap` because it satisfies the non-mutation contract by construction.

## Architecture

A single file: `samples/recap.py`. Runnable directly via `uv run python samples/recap.py`.

### Components

1. **LLM + Agent + Conversation setup.** Same pattern as `samples/set_confirmation_policy.py`: load `.env`, build an `LLM` for Minimax via OpenRouter, register `TerminalTool` and `FileEditorTool`, construct `Conversation(agent=agent, workspace=os.getcwd())`. Set `NeverConfirm()` policy. No security analyzer.

2. **`IDLE_SECONDS` constant.** Default `5.0`. The delay after agent activity ends before an auto-recap fires.

3. **`RECAP_PROMPT` constant.** A fixed prompt string asking for the four labeled sections, with explicit instructions to use file paths and commands verbatim from history and to write `(nothing)` for empty sections.

4. **`print_recap(conversation)` function.** Calls `conversation.ask_agent(RECAP_PROMPT)`, wraps the result in a Unicode box-drawing card frame with a `Recap` header, prints to stdout. Catches any exception from `ask_agent` and prints `(recap failed: <error>)` instead of propagating. Safe to call from a background thread because `ask_agent` is documented thread-safe.

5. **Idle timer (`threading.Timer`).** A single module-level reference holds the currently-armed timer (or `None`). Two small helpers:

   ```
   _idle_timer: threading.Timer | None = None

   def arm_idle_timer(conversation):
       global _idle_timer
       cancel_idle_timer()
       _idle_timer = threading.Timer(
           IDLE_SECONDS, lambda: print_recap(conversation)
       )
       _idle_timer.daemon = True
       _idle_timer.start()

   def cancel_idle_timer():
       global _idle_timer
       if _idle_timer is not None:
           _idle_timer.cancel()
           _idle_timer = None
   ```

   The timer is armed only after `conversation.run()` returns (i.e., the agent has produced something worth recapping). It is canceled the moment the user submits their next line. `daemon=True` ensures process exit doesn't hang on a pending timer.

6. **REPL loop.** Plain `input("> ")`, no `select`, no inner loop:

   ```
   while True:
       try:
           line = input("> ").strip()
       except EOFError:
           break
       cancel_idle_timer()  # next submit kills any pending recap
       if line == "/quit": break
       if line == "/recap":
           print_recap(conversation)
           continue
       if not line: continue
       conversation.send_message(line)
       conversation.run()
       arm_idle_timer(conversation)
   cancel_idle_timer()
   ```

### Data flow

```
                ┌─────────────────────────────┐
                │ REPL: input("> ")           │  (blocking, main thread)
                └──────┬──────────────────────┘
                       │
   ┌───────────────────┼───────────────────┬──────────────────────┐
   │                   │                   │                      │
"/recap"            "/quit"/EOF        empty line              other text
   │                   │                   │                      │
   ▼                   ▼                   ▼                      ▼
cancel timer        cancel timer        cancel timer          cancel timer
print_recap         exit                continue              send_message
   │                                                           run()
   │                                                           arm_idle_timer ──► (background thread,
   │                                                                              fires after 5s,
   │                                                                              calls print_recap)
   └───────────────────────────────────────────────────────────┘
                       │
                       ▼
                  (loop back to input)
```

### File layout impact

- New file: `samples/recap.py`.
- No changes to `pyproject.toml` — `openhands-sdk`, `openhands-tools`, `python-dotenv` are already present.
- No changes to `.env.example` — `OPENROUTER_API_KEY` is already declared.
- No new modules under `samples/` beyond `recap.py`. `print_recap` and helpers live top-of-file.

## Error handling

| Condition | Behavior |
|---|---|
| `KeyboardInterrupt` at REPL input prompt | Clean exit. `cancel_idle_timer()` called in a `finally` block (or after the loop) so a pending recap doesn't fire post-exit. Reuse the `signal.signal(SIGINT, ...)` pattern from `set_confirmation_policy.py`. |
| `KeyboardInterrupt` mid-`conversation.run()` | Break the REPL loop. Cancel timer. Exit. |
| `ask_agent` raises inside `print_recap` (called from timer thread) | Catch in `print_recap`, print `(recap failed: <error>)` inside the card frame. Continue. An uncaught exception in a `Timer` thread would just be swallowed by the default thread excepthook, but we catch defensively. |
| `/recap` typed before any user message | Let `ask_agent` answer normally — the LLM will note an empty session. No special case. |
| Timer fires while the user is mid-typing (partial line, no newline) | Card prints to stdout from the timer thread. Partial input remains in the terminal driver's buffer and is preserved when the user resumes typing. Visual is imperfect but functional; acceptable for a sample. |
| Race: timer fires at the same moment the user submits a line | Possible. `Timer.cancel()` only prevents firing if the timer hasn't already started running its target. If the recap runs concurrently with `send_message` / `run`, `ask_agent`'s thread-safety guarantee holds, so the worst case is one extra recap card printed late. Acceptable. |
| Multiple `run()` calls before any idle fires (e.g., user submits twice quickly) | Each `arm_idle_timer` calls `cancel_idle_timer` first, replacing the previous timer. Only the most recent arm is active. |

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
... agent runs TerminalTool, run() returns ...
... (idle timer armed) ...
>             ← user steps away, 5s passes, timer fires in background
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
              ← no further recaps until the next run() returns

> Create a file recap_demo.txt with the line "hello recap".
              ← user types this within 5s; timer cancellation is no-op
                (timer already fired above). New timer arms after run().
... agent runs FileEditorTool, run() returns ...
>             ← user steps away again, 5s passes
┌─ Recap ────────────────────────────────────────────────────
│ ## What you did
│ - Listed files in the directory, then created recap_demo.txt.
│ ## What changed
│ - New file recap_demo.txt added with one line of content.
│ ## What's next
│ - Confirm contents or extend the file further.
│ ## Files touched
│ - recap_demo.txt
└────────────────────────────────────────────────────────────

> /recap     ← manual; prints immediately, no agent activity
(card regenerated from current state, no new events)

> /quit
```

## Testing

This is a playground sample. Verification is manual:

1. `uv run python samples/recap.py` starts and prompts.
2. Issue an instruction, let the agent finish, then wait 5s without typing — an auto-recap card prints from the timer thread.
3. Wait another 5s — no second recap fires (the timer has already fired and isn't re-armed until the next `run()` returns).
4. Submit a new line within 5s of the previous `run()` — confirm the pending timer is canceled and no recap appears.
5. After the next `run()`, wait 5s — fresh recap fires.
6. `/recap` on demand produces a card without sending a message to the agent.
7. `conversation.state.events` length is unchanged across `print_recap` calls (the non-mutation contract). Spot-check by adding a temporary `print(len(conversation.state.events))` during dev.
8. `Ctrl-C` at the prompt exits cleanly without leaving a pending timer (process exits even if cancel was missed, because the timer is `daemon=True`).

No automated tests. The playground does not have a test harness, and adding one for one sample is out of scope.

## Open questions

None. Architecture, primitive, trigger, and prompt are all settled.

## Future extensions (not in scope)

- Configurable `IDLE_SECONDS` via env var (e.g. `RECAP_IDLE_SECONDS`).
- Raw-mode keystroke detection so partial typing also defers the timer.
- `FinishAction`-based trigger as an additional auto-fire path, alongside the idle timer.
- `/compact` sample using `Conversation.condense()`.
- Cross-session resume: persist the last recap to disk and inject it as the opening assistant message in a fresh conversation.
- Plug the recap into a non-REPL agent loop (e.g., after each high-level task in a longer plan).
