# Noah Architecture

Noah is a voice layer over your operating system, like Jarvis. You say "Noah, set up a new Python project called scraper and open it in VS Code", and it hears you, writes a checklist of what it will do, works through it on your machine, and keeps you updated out loud as each step finishes.

This document covers how Noah is built: the target machine, the components, the tech stack, how a request flows through the system, and the order we build it in.

## Goals

- **Runs smoothly on the target laptop.** This is the main requirement. Noah must stay light on RAM, keep the desk quiet, and not drain the battery while it waits for the wake word. See [Target machine](#target-machine).
- **Hands-free:** a wake word starts a conversation. No keyboard needed.
- **Fast:** from the end of your sentence to Noah's first spoken word should take about 1.5 seconds or less.
- **Acts, not just talks:** Noah controls the OS through a defined set of tools: apps, files, shell, media, volume, windows, clipboard and notifications.
- **Keeps you updated:** for any task with more than one step, Noah makes a checklist and reports progress against it. See [Tasks and progress updates](#8-tasks-and-progress-updates).
- **Safe by default:** Noah never runs a destructive action without a spoken confirmation.
- **Swappable parts:** the LLM, speech-to-text and text-to-speech each sit behind an interface, so changing provider means changing config, not code.

## Non-goals (for now)

- Seeing and clicking on the screen (computer-use style GUI automation). Noah acts through commands and APIs, not mouse clicks.
- A GUI app. Noah is a background daemon with desktop notifications and an optional small status indicator.
- Running the LLM locally. The laptop cannot run a good tool-calling model (see below), so the brain is a cloud call.
- macOS and Windows. The OS-specific code is kept in one layer so they can be added later.

## Target machine

Pulled from the laptop on 2026-10-14:

| Part | Spec | What it means for Noah |
|---|---|---|
| Model | HP Pavilion Gaming 15-ec1xxx | Gaming laptop: the fans get loud under load, which the mic will hear |
| OS | Pop!_OS 22.04, kernel 6.17 | Ubuntu 22.04 base, so system packages are older. Install Python with `uv`, not apt |
| Desktop | GNOME (Pop Shell) on **X11** | Use X11 tools: `xclip`, `xdotool`, `wmctrl`. No Wayland portals needed |
| CPU | AMD Ryzen 5 4600H, 6 cores / 12 threads, AVX2 | Enough to run the wake word, VAD, Piper TTS and a small Whisper model on the CPU |
| RAM | 14 GB, with only about **5 GB free** during normal use (Chrome, VS Code, MySQL, Claude Desktop) | **The tightest limit.** Noah's whole process must stay under about 1.5 GB |
| GPU | NVIDIA GTX 1650 Mobile, **4 GB VRAM**, driver 580, plus AMD Radeon integrated | Fast for Whisper. Too small for a useful local LLM. No CUDA toolkit installed, so get CUDA libraries from pip |
| Graphics mode | `nvidia` (system76-power) | The dGPU is always on in this mode, so using it for Whisper costs little extra. In `hybrid` mode, CUDA would wake the dGPU and drain the battery |
| Audio | PipeWire 1.0.3, Realtek ALC285, built-in mic | `wpctl` works for volume. The built-in mic sits near the fans, so a headset helps in noisy moments |
| Disk | 119 GB free | Plenty for models (about 2–3 GB total) |
| Power | Battery `BAT0`, charger `ACAD` | Noah can read `/sys/class/power_supply/ACAD/online` to switch to battery-friendly settings |

### How Noah stays light on this laptop

- **One process, models loaded once.** No separate servers or containers.
- **Memory budget (target):**

  | Component | RAM |
  |---|---|
  | Python runtime and app code | about 150 MB |
  | openWakeWord + Silero VAD (ONNX) | about 100 MB |
  | faster-whisper `base.en`, int8, on CPU (battery) | about 150 MB |
  | Piper voice (medium quality) | about 100 MB |
  | **Total** | **under 1 GB** |

  With the GPU profile, Whisper moves to VRAM and RAM use drops further.
- **Idle cost is only the wake word.** While idle, only the wake word model runs on small audio frames, which should use a few percent of one CPU core. Whisper, TTS and the LLM run only after the wake word fires.
- **No local LLM.** A 4 GB GPU can only hold small models with weak tool calling, and loading one would use the RAM we don't have. OpenRouter handles the brain.
- **Power profiles** (measured on this laptop in M6; see `[stt]` in `config.example.toml`):
  - On charger: Whisper `small.en`, `int8_float16`, on the GTX 1650. About 0.47 s per command and 425 MB VRAM. The 1650 has no tensor cores, so `large-v3-turbo` took 1.8 s or more and isn't worth it.
  - On battery: Whisper `base.en` int8 on the CPU, 6 threads. About 0.55 s per command. `small.en` on the CPU took 1.5 s.
  - Measured RAM with everything loaded on the GPU profile is about 1 GB. Most of it is the CUDA libraries.
  - Noah picks the profile when it starts and when the charger is plugged in or removed. A config option can force either one.

### System packages to install

```bash
sudo apt install portaudio19-dev playerctl libnotify-bin brightnessctl xdotool wmctrl
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv, then: uv python install 3.13
```

Already present: `wpctl`, `xclip`, `gtk-launch`, `xdg-open`.

## Where OpenRouter fits

OpenRouter is the **brain**, not the hands. It is a gateway that gives one OpenAI-compatible API to hundreds of LLMs (Claude, GPT, Gemini, Llama and others) with tool calling. It does not touch your PC.

Noah's own code does the PC interaction. The LLM decides *which tool to call with which arguments*, and Noah's tool layer runs it locally. This split keeps Noah safe and portable: the model can only do what the tools allow.

OpenRouter also lets us switch models per task (a fast cheap model for simple commands, a stronger one for multi-step tasks) without changing code.

## High-level design

```
 ┌──────────────────────────────── Noah daemon (one asyncio process) ───────────────────────────────┐
 │                                                                                                   │
 │  Mic ──► Wake word ──► VAD ──► STT ──► text ──► Agent ──► reply text ──► TTS ──► Speaker          │
 │        (openWakeWord) (Silero) (faster-whisper)   │  ▲                   (Piper)    ▲             │
 │                                                    │  │                             │             │
 │                                       tool calls ▼  │ results          progress     │             │
 │                         ┌──────────────┐   ┌──────────────────┐   announcements    │             │
 │                         │ Task manager │◄──│  Safety gate      │──►┌──────────────┐ │             │
 │                         │ (checklists, │   │ (allow/confirm/   │   │ Tool registry│ │             │
 │                         │  progress)   │───┼───────────────────┼───┼──────────────┼─┘             │
 │                         └──────┬───────┘   └──────────────────┘   └──────┬───────┘               │
 │                                │ SQLite                                   │                       │
 └────────────────────────────────┼──────────────────────────────────────────┼───────────────────────┘
          │ HTTPS                 ▼                                          │ subprocess / D-Bus
          ▼                ~/.local/share/noah/noah.db                       ▼
     OpenRouter API                                                   Operating system
```

Everything runs in one long-lived Python process, started as a systemd user service at login.

## Components

### 1. Audio input

- **Library:** `sounddevice` (PortAudio) reads 16 kHz mono frames from the mic.
- One audio stream feeds the wake word detector, VAD and recorder, so the mic is opened once.
- PipeWire's built-in noise suppression (or `noise-suppression-for-voice`) can be added if fan noise causes false wake-ups or bad transcripts.

### 2. Wake word

- **Choice:** `openWakeWord`. Open source, light on CPU, and supports training a custom "Hey Noah" model.
- **Alternative:** Picovoice Porcupine. More accurate out of the box and lighter still, but needs an API key and has licensing limits.
- **Also supported:** a global hotkey for push-to-talk (`pynput` on X11). Useful for development and noisy rooms.

### 3. Voice activity detection (VAD)

- **Choice:** Silero VAD (ONNX, CPU). The model file ships with faster-whisper, so no torch is needed. `audio/vad.py` runs it one 32 ms frame at a time.
- After the wake word, it decides when you have **stopped speaking**, so recording stops without a fixed timeout.
- While Noah is speaking, it detects you talking over Noah (**barge-in**): Noah stops talking and listens.

### 4. Speech-to-text (STT)

- **Choice:** `faster-whisper`, local, with the two power profiles above.
- **GPU setup:** no CUDA toolkit is installed, so install the CUDA 12 cuBLAS and cuDNN 9 libraries from pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) as an optional `gpu` extra. Nothing system-wide changes.
- **Cloud fallback:** Groq's hosted Whisper, if local transcription is ever too slow.
- Interface: `transcribe(audio) -> str`.

### 5. Agent (the brain)

- **API:** OpenRouter through the official `openai` Python SDK with `base_url="https://openrouter.ai/api/v1"`.
- **Tool calling:** use the model's native function calling, with tools described as JSON Schema. This replaces the hand-written START/PLAN/OUTPUT JSON protocol from the old prototype. Native tool calling is more reliable and supports several tools in one turn.
- **Loop:**
  1. Send the conversation and the tool list.
  2. If the model returns tool calls, send each one through the safety gate, run it, append the result, and go back to step 1.
  3. If the model returns text, that is the reply. Stream it to TTS.
  4. Stop after a maximum number of steps (for example 8 for direct requests, more for checklist tasks) so a confused model cannot loop forever.
- **Planning first:** for a request with more than one step, the system prompt tells the model to call `create_checklist` before doing anything else (see section 8).
- **Streaming:** the model's text is streamed and spoken one sentence at a time, so Noah starts talking before the full reply exists. This is the biggest latency win.
- **Context:** the system prompt carries live context: date and time, OS and desktop, user name, power state, active window, and the open checklists. The conversation is kept for the session (for example until 2 minutes of silence) and then cleared. Checklists outlive the session.
- **Model routing:** set in config. Start with one fast tool-calling model. Add a "smart" model later for long tasks.

### 6. Safety gate

Every tool call passes through this gate before it runs. Each tool declares a risk level:

| Level | Examples | Behaviour |
|---|---|---|
| `safe` | read the time, list files, get volume, open an app | Runs immediately |
| `confirm` | write or move files, kill a process, run an arbitrary shell command, install packages | Noah says what it is about to do and waits for a spoken "yes" |
| `deny` | `rm -rf /`, `sudo`, `mkfs`, disk writes, editing system files | Never runs. Noah explains why |

- The shell tool matches commands against a denylist and an allowlist. Unknown commands default to `confirm`.
- Commands run with a timeout, without `sudo`, and their output is truncated before it goes back to the model.
- Every action is written to an audit log (`~/.local/state/noah/actions.log`).
- When a checklist task needs confirmation, it pauses on that item and Noah asks. The rest of the checklist waits.

### 7. Tools (the hands)

Tools are Python functions with type hints. A decorator registers each one, builds its JSON Schema from the signature and docstring, and records its risk level.

```python
@tool(risk="safe")
def set_volume(percent: int) -> str:
    """Set the system output volume, 0-100."""
```

All OS-specific code lives in tools, behind a small `platform` module, so the agent never knows which OS it is on.

**Starter set for this laptop (Pop!_OS, GNOME on X11):**

| Tool | Implementation |
|---|---|
| `open_app(name)` | Search `.desktop` files, launch with `gtk-launch` |
| `open_path_or_url(target)` | `xdg-open` |
| `run_shell(cmd)` | `asyncio.create_subprocess_shell`, captures stdout and stderr, with a timeout |
| `list_files`, `read_file`, `write_file`, `move_file` | Python `pathlib`, limited to the home directory |
| `set_volume`, `get_volume`, `mute` | `wpctl` (PipeWire) |
| `set_brightness(percent)` | `brightnessctl` |
| `media_control(action)` | `playerctl` (play, pause, next, previous) |
| `notify(title, body)` | `notify-send` |
| `clipboard_get`, `clipboard_set` | `xclip` |
| `focus_window(name)`, `list_windows()` | `wmctrl`, `xdotool` |
| `system_info()` | `psutil` (battery, CPU, memory, disk) |
| `web_search(query)` | OpenRouter's web plugin, so no second API key |
| `screenshot()` | `ffmpeg -f x11grab` (`gnome-screenshot` isn't installed) |
| `set_verbosity`, `do_not_disturb` | Voice controls: "keep it quiet", "do not disturb" |

**Later: MCP.** Noah will be able to act as an MCP (Model Context Protocol) client and load tools from any MCP server (calendar, email, GitHub, Home Assistant and so on), so it gains abilities without new code. The internal tools use the same interface, so both look the same to the agent.

### 8. Tasks and progress updates

This is how Noah keeps you updated, Jarvis style.

**When a checklist is made.** For any request that needs more than one step (or any step that takes more than a few seconds), the agent first calls `create_checklist`. Simple requests like "what time is it" skip this.

**What a checklist looks like:**

```
Task: Set up Python project "scraper"           status: running   2/5
  [x] Create ~/codes/scraper
  [x] Initialise a uv project
  [~] Add requests and beautifulsoup4           ← in progress
  [ ] Initialise git
  [ ] Open in VS Code
```

Each item has a status: `pending`, `in_progress`, `done`, `failed`, `waiting` (needs your confirmation or input) or `skipped`.

**Agent tools for checklists:**

| Tool | Purpose |
|---|---|
| `create_checklist(title, items)` | Make the plan before starting |
| `update_item(task_id, item_id, status, note?)` | Mark progress as the agent works |
| `add_items(task_id, items)` | Add steps it discovers along the way |
| `get_tasks(status?)` | Answer "what are you working on?" |

**How Noah reports progress.** The task manager emits an event on every status change. A small announcer turns events into short spoken lines from templates, without an extra LLM call, so updates are instant and free:

| Moment | What Noah says |
|---|---|
| Checklist created | "Got it. Five steps: create the folder, set up the project, add the packages, set up git, and open VS Code." |
| Item done | "Two of five done. Project's set up." |
| Item waiting | "I need your OK to install two packages. Go ahead?" |
| Item failed | "Couldn't install beautifulsoup4: no network. Want me to retry or skip it?" |
| Task done | "All done. Scraper is open in VS Code." |

The same events also show as desktop notifications (`notify-send`), which update in place, so you can glance at progress without listening.

**Verbosity** is set in config and can be changed by voice ("Noah, keep it quiet"):

- `quiet`: speak only when a task starts, finishes, fails or needs you. Everything else goes to notifications.
- `normal` (default): also speak each finished item, but group items that finish within a few seconds of each other into one update.
- `chatty`: speak every status change.

**Not interrupting you.** Announcements go into a queue. Noah does not speak over you, and it holds updates while you are talking or while it is listening. A "do not disturb" mode sends everything to notifications only.

**Background tasks.** Long checklists run as background asyncio tasks, so you can keep talking to Noah, or start another task, while one runs. You can ask:
- "Noah, status?" for a spoken summary of every running task.
- "What's left on the project setup?" for the remaining items.
- "Cancel that" to stop the current task cleanly, marking the remaining items `skipped`.

**Storage.** Tasks and items are stored in SQLite (`~/.local/share/noah/noah.db`), so a checklist survives a restart and you can ask "what did you do this morning?". On restart, unfinished tasks are reported, not resumed automatically.

### 9. Text-to-speech (TTS)

- **Choice:** Piper. Local, CPU-only, very fast, about 100 MB per voice. Generates speech much faster than real time on the 4600H.
- **Higher-quality option:** Kokoro (82M parameters). Runs on the GTX 1650 or the CPU when the charger is in.
- **Cloud option:** ElevenLabs, for the most natural voice.
- Plays sentence by sentence as the agent streams. Playback can be cancelled at once for barge-in.
- Interface: `speak(text_stream)` and `stop()`.

### 10. Orchestrator (state machine)

The orchestrator owns the conversation state and connects the components:

```
IDLE ──wake word──► LISTENING ──silence──► TRANSCRIBING ──► THINKING ⇄ ACTING ──► SPEAKING
  ▲                     ▲                                                          │
  │                     └──────────────── barge-in / follow-up ────────────────────┤
  └──────────────────────────── session timeout ───────────────────────────────────┘

Background: task runners ──events──► announcer queue ──► SPEAKING (only when IDLE)
```

After Noah speaks, it listens for a few seconds for a follow-up without needing the wake word again. A short chime plays when Noah starts listening, so you know it heard you.

### 11. Interfaces for development

- `noah run` starts the voice daemon.
- `noah chat` is a text-only REPL using the same agent, tools and task manager, with no audio. Most development and testing happens here.
- `noah say "turn the volume down"` runs one command and exits.
- `noah tasks` prints the current checklists in the terminal.

## Tech stack summary

| Concern | Choice |
|---|---|
| Language | Python 3.13 (installed with `uv`; the system has 3.12 through pyenv) |
| Package and env manager | `uv` |
| Concurrency | `asyncio` |
| LLM | OpenRouter through the `openai` SDK |
| Wake word | `openWakeWord` (Porcupine as an alternative) |
| VAD | Silero VAD |
| STT | `faster-whisper`: GPU on charger, CPU on battery |
| TTS | Piper (Kokoro or ElevenLabs as alternatives) |
| Audio I/O | `sounddevice` |
| Task storage | SQLite (`aiosqlite`) |
| Notifications | `notify-send` (libnotify) |
| Hotkey | `pynput` |
| Tool schemas and config validation | `pydantic` |
| Config | `~/.config/noah/config.toml` plus `.env` for API keys |
| CLI | `typer` |
| Logging | standard `logging` |
| System info | `psutil` |
| Tests | `pytest`, `pytest-asyncio` |
| Lint and format | `ruff` |
| Type checking | `pyright` |
| Running at login | systemd user service |

## Project layout

```
noah/
├── docs/
│   └── architecture.md
├── src/noah/
│   ├── __main__.py         # CLI entry point (typer)
│   ├── config.py           # load and validate config.toml and .env
│   ├── orchestrator.py     # state machine, connects all components
│   ├── power.py            # charger detection, picks the STT/TTS profile
│   ├── audio/
│   │   ├── input.py        # mic stream
│   │   ├── wakeword.py
│   │   ├── vad.py
│   │   └── player.py       # playback with cancel support
│   ├── stt/                # base.py interface + faster_whisper.py, groq.py
│   ├── tts/                # base.py interface + piper.py, kokoro.py
│   ├── agent/
│   │   ├── llm.py          # OpenRouter client, streaming
│   │   ├── loop.py         # tool-calling loop
│   │   └── prompts.py
│   ├── tasks/
│   │   ├── models.py       # Task, Item, Status
│   │   ├── store.py        # SQLite persistence
│   │   ├── manager.py      # create/update, background runners, events
│   │   └── announcer.py    # events → spoken lines + notifications, verbosity, queue
│   ├── tools/
│   │   ├── registry.py     # @tool decorator, schema generation
│   │   ├── safety.py       # risk levels, allow/deny rules, confirmation
│   │   ├── apps.py, shell.py, files.py, media.py, windows.py, system.py, checklist.py
│   │   └── platform/       # linux_x11.py now, others later
│   └── memory/             # session history, later long-term memory
├── tests/
├── pyproject.toml
└── .env.example
```

## Request flow example

"Hey Noah, set up a Python project called scraper with requests and beautifulsoup, and open it in VS Code."

1. The wake word fires. A chime plays and the state changes to LISTENING.
2. VAD detects the end of speech after about 700 ms of silence.
3. faster-whisper transcribes the audio (about 300 ms on the GPU, about 800 ms on the CPU).
4. The agent sends the text and tools to OpenRouter. The model calls `create_checklist` with five items.
5. Noah says: "Got it. Five steps: create the folder, set up the project, add the packages, set up git, and open VS Code." The task starts running in the background.
6. The agent runs `run_shell("mkdir -p ~/codes/scraper")` and marks item 1 `done`, then runs `uv init` and marks item 2 `done`. Noah says: "Two of five done. Project's set up."
7. `uv add requests beautifulsoup4` is a `confirm` action. Item 3 changes to `waiting`, and Noah asks: "I need your OK to install two packages. Go ahead?" You say "yes".
8. The packages install, git is initialised, and `open_app("code ~/codes/scraper")` runs.
9. Noah says: "All done. Scraper is open in VS Code." The notification shows 5/5.

## Latency budget

| Stage | Target |
|---|---|
| End-of-speech detection | 500–700 ms |
| STT | 200–400 ms on GPU, 600–900 ms on CPU |
| LLM first token (fast model) | 300–700 ms |
| Tool execution (typical) | under 200 ms |
| TTS first audio (Piper) | under 150 ms |
| **Speech end to first spoken word** | **about 1.5 s on charger, about 2 s on battery** |

For checklist tasks, the first spoken line is the plan, so you hear a response within the same budget even if the whole task takes minutes.

## Privacy

- Audio never leaves the machine. Only the transcribed text, tool results and checklist state go to OpenRouter.
- The wake word runs locally, so nothing is recorded or sent until it fires.
- OpenRouter lets you restrict providers and opt out of data logging. Noah's config should set this.
- Keep tool output sent to the model small, and strip secrets such as `.env` contents.

## Build phases

1. **Text agent with checklists.** `noah chat` with OpenRouter, native tool calling, the tool registry, the safety gate, the task manager (SQLite and checklist tools) and 5–6 core tools. Progress updates print in the terminal and show as notifications. This proves the brain, the hands and the checklist.
2. **Voice, push-to-talk.** Add mic input, faster-whisper with power profiles, and Piper behind a hotkey. Stream replies sentence by sentence. The announcer starts speaking updates.
3. **Hands-free.** Add wake word, VAD, follow-up listening, barge-in and the announcement queue rules.
4. **Daily use.** systemd service, config file, audit log, more tools, verbosity and do-not-disturb by voice, background tasks with "status?" and "cancel".
5. **Extend.** MCP client, long-term memory, model routing, a tray indicator, and other operating systems.

## Open decisions

1. **Default LLM on OpenRouter.** It needs strong tool calling, good planning for checklists and a fast first token. Benchmark 2–3 candidates on a set of real commands.
2. **Wake word engine.** openWakeWord (free, train our own "Hey Noah") or Porcupine (easier, licensed).
3. **How much power `run_shell` gets.** Confirm every command, or allow a list of known-safe commands (for example `ls`, `git status`, `uv add`) to run without asking.
4. **Graphics mode.** The laptop is in `nvidia` mode, so the GPU profile is cheap. If you switch to `hybrid` for battery life, Noah should use the CPU profile on battery.
5. **Mic.** Test the built-in mic with the fans running. If wake word accuracy is poor, add noise suppression or use a headset.
