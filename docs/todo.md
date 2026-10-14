# Noah TODO

Where the build stands, and what to do next. Read this first when picking the project up in a new session, after `CLAUDE.md`, [architecture.md](architecture.md) and [build-plan.md](build-plan.md).

Last updated: 2026-10-14, at the end of M3.

## Status

| Milestone | Status | Branch |
|---|---|---|
| M0 Project skeleton | Done, merged | `m0-skeleton` |
| M1 Agent loop (text) | Done, merged | `m1-agent-loop` |
| M2 Tools and safety | Done, merged | `m2-tools-safety` |
| M3 Checklists and progress updates | Done, committed on its branch | `m3-checklists` |
| M4 Pick the default model | **Next** | `m4-model-choice` (stacked on `m3-checklists`) |
| M5 Noah speaks | Not started | |
| M6 Noah listens (push-to-talk) | Not started | |
| M7 Hands-free | Not started | |
| M8 Daily driver | Not started | |
| M9 Extend | Not started | |

Before starting M3, check that `m2-tools-safety` is merged into `main` (`git log main --oneline`). If it isn't, ask the user rather than building on top of uncommitted work.

## How we work

- The user commits, pushes and merges each milestone themselves. Don't commit unless they ask.
- One branch per milestone. A milestone is done when its "done when" check in `build-plan.md` passes, and `ruff format`, `ruff check`, `pyright` and `pytest` all pass.
- A live check against OpenRouter is fine: the key is in `.env`, and each call costs a fraction of a cent. Don't run actions that visibly change the user's desktop (opening windows, changing settings) without asking. Put anything you change back afterwards, such as the volume.
- `uv` is in `~/.local/bin`. If it's not on `PATH`, run `source ~/.local/bin/env`.

## Decisions made so far

These aren't spelled out in `architecture.md`:

- **Model:** the default is `openrouter/auto` until M4 benchmarks a real choice. Override it with `[llm] model` in `~/.config/noah/config.toml`.
- **Messages:** messages and tool schemas are plain OpenAI-format dicts, not SDK types.
- **System prompt:** rebuilt on every model call and never stored in `Agent.history`.
- **LLM failures:** if the model call fails, the whole turn is rolled out of history.
- **Tool failures:** tool errors go back to the model as `Error: ...` text, never as exceptions.
- **Shell policy** (`tools/safety.py`):
  - Deny patterns are checked first.
  - Anything with shell operators (`; & | < > \` $`) or a secret path needs confirmation.
  - Only a read-only allowlist runs without asking: `ls`, `cat`, `df`, `free`, `git status`/`log`/`diff` and similar.
- **File tools:** limited to the home folder. Secret paths (`.ssh`, `.gnupg`, `.aws`, `.env*` except `.env.example`, `.netrc` and similar) are always denied.
- **Confirmation in text mode:** a y/n prompt. With no terminal to ask in (piped input), Noah declines.
- **Wake word:** M7 starts with openWakeWord's pretrained "Hey Jarvis" model. A custom "Hey Noah" model comes in M8.
- **Tests:** the openai SDK (3.x) now uses `httpx2`, not `httpx`, so tests that build SDK errors import `httpx2`.

## Known issues and follow-ups

- [ ] **Declining without a terminal reads as the user saying no.** The model is told "the user declined", so it replies "No problem, I didn't create it". The registry should be able to tell "couldn't ask" from "said no". Fix this when voice confirmation lands in M7, or earlier if it gets in the way.
- [ ] **Extra blank line in chat.** When the model writes text ending in `\n\n` before a tool call, `noah chat` prints an empty line before the `↳ tool(...)` line. Cosmetic; it's in `chat.py:_Renderer`.
- [ ] **Preamble before tool calls.** The model often says "I'll check that for you." before calling a tool. That's fine in text but will be spoken in M5. Consider a prompt rule or skipping it in TTS then.
- [ ] **`open_app` hasn't been tried with the real model,** to avoid opening windows unasked. Ask the user to confirm "open Firefox" works in `noah chat`.
- [ ] **Missing system packages on the laptop:** `notify-send`, `playerctl`, `brightnessctl`, `wmctrl` and PortAudio. The `notify` tool fails until `libnotify-bin` is installed. Ask the user to run:
  ```bash
  sudo apt install portaudio19-dev libnotify-bin playerctl brightnessctl wmctrl
  ```
- [ ] **No `platform/` layer yet.** Tools call Linux commands directly. Add the layer only when a second OS is actually targeted.
- [ ] **`architecture.md` still has the old text sketch.** The proper diagrams are in a separate page (https://claude.ai/artifact/RScKGkpPUc6rnBrfwf49ZN). Updating that page needs it read first from the new session, because it was published from this one. Maybe add Mermaid versions to the docs later.
- [ ] **Starter tools from `architecture.md` not built yet:** `media_control`, `set_brightness`, clipboard, windows, `screenshot`, `web_search`, and writing or moving files. These are planned for M8. Writing and moving files should be `confirm`.

## M3: what was built

- `tasks/models.py`, `store.py`, `manager.py`, `announcer.py`, `notifier.py`, and `tools/checklist.py` (`create_checklist`, `update_item`, `add_items`, `get_tasks`, `cancel_task`).
- Item ids the model sees are the 1-based item numbers within a task, not database ids.
- `session.py:start()` wires everything; `noah chat` and `noah say` use it.
- The step limit is a callable: `max_steps` (8) normally, `max_steps_with_checklist` (40) while a checklist is open. It only ever rises within a turn, so finishing the checklist doesn't cut off the summary.
- Confirmation inside a checklist: `TaskManager.gate` marks the in-progress item `waiting` (with `asking=True`, so the announcer flushes grouped progress but doesn't add its own line), asks, then sets it back to `in_progress`.
- Checklist tool calls are hidden in `noah chat`; the cyan `●` announcer lines replace them.
- Live check passed: "make a folder called test-noah in codes, put a README in it, and init git" made a 3-step checklist, asked before each shell command, announced progress, notified, and `noah tasks` shows it after restart.

## M4 (next)

 write `scripts/eval_models.py` and benchmark 3–4 OpenRouter models on about 25 real commands (single-step, multi-step and should-refuse). Score each on the right tool and arguments, checklist quality, refusals, time to first token and cost.
