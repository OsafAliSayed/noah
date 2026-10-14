# Noah TODO

Where the build stands, and what to do next. Read this first when picking the project up in a new session, after `CLAUDE.md`, [architecture.md](architecture.md) and [build-plan.md](build-plan.md).

Last updated: 2026-10-14, at the end of M2.

## Status

| Milestone | Status | Branch |
|---|---|---|
| M0 Project skeleton | Done, merged | `m0-skeleton` |
| M1 Agent loop (text) | Done, merged | `m1-agent-loop` |
| M2 Tools and safety | Done, **waiting for the user to commit and merge** | `m2-tools-safety` |
| M3 Checklists and progress updates | **Next** | `m3-checklists` (to create from `main` after M2 merges) |
| M4 Pick the default model | Not started | |
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

## M3: Checklists and progress updates (next)

Goal and "done when" are in `build-plan.md`. The detailed design is in `architecture.md` section 8. Implementation notes:

### Files to add

- `src/noah/tasks/models.py`: `Task` (id, title, status, created and updated times) and `Item` (id, task_id, position, text, status, note). Status is one of `pending | in_progress | done | failed | waiting | skipped`. A task's status is derived from its items: running, done, failed or waiting.
- `src/noah/tasks/store.py`: SQLite at `settings.data_dir / "noah.db"`, using `aiosqlite`, which needs adding with `uv add aiosqlite`. Create tables on first use. Keep the schema small and versioned with `PRAGMA user_version`.
- `src/noah/tasks/manager.py`: `create`, `update_item`, `add_items` and `get_tasks`. Every status change emits an event to subscribers through a callback list or an `asyncio.Queue`.
- `src/noah/tools/checklist.py`: the agent-facing tools `create_checklist(title, items)`, `update_item(task_id, item_id, status, note?)`, `add_items(task_id, items)` and `get_tasks(status?)`. All are `safe`. They need the manager, so build them with a factory, e.g. `checklist_tools(manager) -> list[Tool]`, and add them in `chat.build_agent`. Keep `default_tools()` stateless.
- `src/noah/tasks/announcer.py`: turns events into short lines from templates (see the table in `architecture.md` §8), without an extra LLM call.
  - Groups items that finish within a few seconds of each other.
  - Applies `settings.tasks.verbosity` (`quiet | normal | chatty`).
  - Prints lines in `noah chat`, without breaking the streamed output (see `_Renderer`).
  - Sends a desktop notification per task, updated in place.

### Notifications that update in place

The laptop runs Ubuntu 22.04's libnotify 0.7.9. Its `notify-send` has no `--replace-id` (that arrived in 0.8), so use `gdbus`, which is installed:

```bash
gdbus call --session --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.Notify \
  "Noah" <replaces_id> "" "<title>" "<body>" "[]" "{}" 5000
# prints "(uint32 42,)": keep 42 and pass it as replaces_id next time
```

### Agent changes

- **Planning rule in the system prompt** (`agent/prompts.py`): for any request with more than one step, call `create_checklist` first, then `update_item` as each step starts and finishes.
- **Step limit:** a checklist task needs more model calls than the current limit of 8. The plan is to raise the limit while a checklist is active, e.g. a `max_steps_with_checklist` setting (`architecture.md` §5).
- **Open checklists:** include them in the system prompt context, so "what's left?" works.

### CLI and tests

- **`noah tasks`:** replace the stub in `cli.py`. It prints current and recent checklists, with each item's status.
- **Background running:** in M3, a checklist runs inside the chat turn. Running in the background, so you can keep talking while it runs, is M8 (`build-plan.md`). Keep the manager's event design ready for it, but don't build the background runner yet.
- **Tests:**
  - Store round-trips, using a `tmp_path` database.
  - Status rules for items and tasks.
  - Announcer templates, grouping and verbosity, with a fake clock.
  - Checklist tools going through `ToolRegistry`.
  - A loop test using `FakeLLM` that scripts create → update → update → final text.

### Done when (from `build-plan.md`)

"Make a folder called test-noah in codes, put a README in it, and init git" should:
- produce a checklist,
- print and notify after each step,
- pause for confirmation where needed,
- finish with a summary.

The checklist must still be there after restarting Noah (`noah tasks`).

## After M3

M4 comes next: write `scripts/eval_models.py` and benchmark 3–4 OpenRouter models on about 25 real commands (single-step, multi-step and should-refuse). Score each on the right tool and arguments, checklist quality, refusals, time to first token and cost.
