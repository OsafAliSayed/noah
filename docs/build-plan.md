# Noah Build Plan

How we build Noah, in order. The design is in [architecture.md](architecture.md). This file is the plan for getting there.

## How we work

- **Small milestones.** Each one ends with something you can run on the laptop and try. No milestone leaves Noah broken.
- **Text first, voice last.** The brain, the tools and the checklists are built and tested by typing. Voice is added on top once they work. Audio bugs are hard to debug, and we don't want them mixed with logic bugs.
- **Testable without a mic or the internet.** The LLM, STT and TTS sit behind interfaces with fake versions for tests, so `pytest` runs offline and fast.
- **Measure on the real laptop.** From milestone 5 on, every run logs time per stage and Noah's RAM use. "Smooth" is checked with numbers, not by feel.
- **Who does what.** Claude writes the code, tests and docs. You run the voice parts on the laptop (Claude can't hear the mic or speakers), give feedback, and approve each milestone.
- **Git.** One branch per milestone, merged to `main` when its "done when" checks pass.

## Before we start (you)

1. Create an OpenRouter account and API key: <https://openrouter.ai/keys>. Add a few dollars of credit. Development should cost very little.
2. Install the system packages:
   ```bash
   sudo apt install portaudio19-dev playerctl libnotify-bin brightnessctl xdotool wmctrl
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

## Milestones

### M0. Project skeleton

- `uv init` with Python 3.13 and a `src/noah` package layout.
- `pyproject.toml` with `ruff`, `pyright`, `pytest` and `pytest-asyncio` set up.
- `.gitignore`, `.env.example` (`OPENROUTER_API_KEY=`), and a `CLAUDE.md` with the dev commands.
- `config.py`: loads `~/.config/noah/config.toml` and `.env` into a pydantic settings model with defaults.
- `typer` CLI with empty `chat`, `run`, `say`, `tasks` and `doctor` commands.
- `noah doctor`: checks Python, the system packages, the API key, the mic, the GPU and charger state, and prints what is missing.

**Done when:** `uv run noah --help` and `uv run noah doctor` work, and `uv run pytest`, `ruff` and `pyright` pass.

### M1. Agent loop (text)

- `agent/llm.py`: OpenRouter client using the `openai` SDK, with streaming and a `FakeLLM` for tests.
- `agent/loop.py`: the tool-calling loop. Send messages and tools, run tool calls, append results, repeat until the model replies with text or hits the step limit.
- `agent/prompts.py`: system prompt with live context (time, OS, user, power state).
- `noah chat`: a REPL that streams replies to the terminal.

**Done when:** you can chat with Noah in the terminal, and tests cover the loop with a fake LLM (tool call → result → final answer, step limit, LLM errors).

### M2. Tools and safety

- `tools/registry.py`: the `@tool(risk=...)` decorator that builds the JSON Schema from type hints and docstrings.
- `tools/safety.py`: `safe` / `confirm` / `deny`, a shell denylist and allowlist, and an audit log. In text mode, confirmation is a `y/n` prompt.
- First tools: `get_time`, `system_info`, `open_app`, `open_path_or_url`, `get_volume`, `set_volume`, `notify`, `run_shell`, `list_files`, `read_file`.
- Starting rule for `run_shell`: every command needs confirmation except a small read-only allowlist (`ls`, `pwd`, `cat`, `git status`, `which`, `df`, `free`). We widen it later based on use.

**Done when:** "open Firefox", "set volume to 30" and "how much disk space do I have" work in `noah chat`. `rm -rf ~` style commands are refused. Unit tests cover schema generation and every safety rule.

### M3. Checklists and progress updates

- `tasks/models.py` and `tasks/store.py`: tasks and items in SQLite with `aiosqlite`.
- `tasks/manager.py`: create and update tasks, emit an event on every status change, run tasks in the background.
- Checklist tools: `create_checklist`, `update_item`, `add_items`, `get_tasks`.
- `tasks/announcer.py`: turns events into short lines from templates, groups quick updates, applies the verbosity level, and sends lines to the terminal and `notify-send` (one notification per task, updated in place).
- System prompt rule: plan with `create_checklist` before any multi-step task.
- `noah tasks`: shows current and recent checklists.

**Done when:** "make a folder called test-noah in codes, put a README in it, and init git" produces a checklist, prints and notifies after each step, pauses for confirmation where needed, and finishes with a summary. The checklist is still there after restarting Noah.

### M4. Pick the default model

- `scripts/eval_models.py`: runs about 25 real commands (single-step, multi-step and "should refuse") against 3–4 OpenRouter models.
- Each run is scored on: right tool and arguments, a sensible checklist, refusing unsafe requests, time to first token, and cost per command.

**Done when:** we have a results table and pick the default model (plus a fallback) in config. This settles open decision 1.

### M5. Noah speaks

- `tts/piper.py`: Piper with one English voice, downloaded on first run.
- `audio/player.py`: playback through `sounddevice` that can be stopped instantly.
- Split the streamed reply into sentences and speak each one as it arrives.
- The announcer speaks progress updates through the same player.
- `noah chat --speak`: type to Noah, and it answers out loud.
- Add the per-stage timing and RAM log.

**Done when:** in `noah chat --speak`, the first spoken word comes within 150 ms of the first sentence arriving, and you're happy with the voice.

### M6. Noah listens (push-to-talk)

- `audio/input.py`: one 16 kHz mic stream.
- `stt/faster_whisper.py`, with power profiles: `large-v3-turbo` on the GTX 1650 when charging, `small.en` on the CPU on battery. The CUDA libraries come from an optional `gpu` extra.
- `power.py`: reads `/sys/class/power_supply/ACAD/online` and switches profiles when the charger is plugged in or removed.
- Global hotkey (`pynput`): hold to talk, release to send.
- `noah run --ptt`: full voice loop with the hotkey.

**Done when:** you can hold the hotkey, give a command, and hear the answer about 1.5 s after release on the charger and about 2 s on battery. Noah's RAM stays under 1 GB.

### M7. Hands-free

- `audio/wakeword.py`: openWakeWord. We start with its pretrained **"Hey Jarvis"** model, which fits the idea and needs no training. A custom "Hey Noah" model comes in M8.
- `audio/vad.py`: Silero VAD for the end of speech and for barge-in.
- `orchestrator.py`: the full state machine. Wake word, listening chime, follow-up window, barge-in, and the announcement queue (no talking over you).
- Spoken confirmations: "Go ahead?" → yes / no.
- Fan noise test: run something heavy and check false wake-ups and transcript quality. Add PipeWire noise suppression if needed.

**Done when:** you can use Noah for an hour without touching the keyboard, with fewer than 1 false wake-up per hour and idle CPU under 5% of one core.

### M8. Daily driver

- A systemd user service that starts Noah at login and restarts it if it crashes.
- More tools: windows (`wmctrl`, `xdotool`), clipboard (`xclip`), media (`playerctl`), brightness, screenshot, web search.
- Voice controls: "keep it quiet", "do not disturb", "status?", "cancel that".
- Train a custom "Hey Noah" wake word.
- A real `config.toml` with sensible defaults and comments.

**Done when:** Noah starts at login, and you use it every day for a week. Problems from that week go into a list and get fixed.

### M9. Extend

Pick from what the week of daily use shows is most needed: MCP client support, long-term memory, routing between a fast and a smart model, a tray indicator, more apps.

## Rough order and size

| Milestone | Size | Can you use it? |
|---|---|---|
| M0 Skeleton | Small | No |
| M1 Agent loop | Small | Text chat |
| M2 Tools and safety | Medium | Text chat that controls the PC |
| M3 Checklists | Medium | Text, with progress updates |
| M4 Model choice | Small | Same, with the best model |
| M5 Speaks | Small | Type, hear answers |
| M6 Listens | Medium | Push-to-talk voice |
| M7 Hands-free | Large | "Hey Jarvis" Noah |
| M8 Daily driver | Medium | Runs all the time |

M0 to M4 are mostly code Claude can write and test on its own. M5 to M7 need you at the laptop to listen and talk.
