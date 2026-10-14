# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Noah is a voice-controlled assistant for the local OS (Jarvis style): wake word → local speech-to-text → LLM via OpenRouter with native tool calling → local tools that act on the OS → local text-to-speech. Multi-step requests become persisted checklists that Noah reports progress on.

- `docs/architecture.md` is the design. Read it before adding a component.
- `docs/build-plan.md` is the milestone plan (M0–M9), each with a "done when" check. Work happens one milestone per branch (`m0-skeleton`, ...).
- `docs/todo.md` is the handoff: current status, decisions made, known issues and notes for the next milestone. Read it at the start of a session and update it at the end of each milestone.

## Commands

```bash
uv sync --extra gpu                       # install deps; the gpu extra adds CUDA libs (uv lives in ~/.local/bin)
uv run noah doctor                        # check system deps, API key, mic, GPU, power
uv run pytest                             # all tests (offline, no mic needed)
uv run pytest tests/test_config.py::test_defaults_when_no_config_file   # one test
uv run ruff format src tests scripts && uv run ruff check src tests scripts
uv run pyright
```

All four (format, lint, pyright, pytest) should pass before a milestone is done.

## How the agent fits together

- `agent/llm.py`: `OpenRouterLLM.stream()` yields `TextDelta`s as they arrive, then complete `ToolCall`s (fragments are assembled by index). Every SDK error becomes `LLMError` with a user-facing message. Messages and tool schemas are plain OpenAI-format dicts.
- `agent/loop.py`: `Agent.run(text)` is an async generator of events (`TextDelta`, `ToolStarted`, `ToolFinished`, `StepLimitReached`). The system prompt is a callable rebuilt on every model call, so it is not stored in `history`. Tool exceptions are fed back to the model as `Error: ...` results; an `LLMError` rolls the whole turn out of `history` and re-raises.
- `ToolExecutor` (in `loop.py`) is implemented by `tools/registry.py:ToolRegistry`, which validates arguments, applies the safety gate, runs the tool, truncates the result and writes the audit log (`~/.local/state/noah/actions.log`).
- A tool is a typed function wrapped in `@tool(risk=...)`; the pydantic model built from its signature gives both the JSON Schema and argument validation. Describe parameters with `Annotated[T, Field(description=...)]`. `risk` is `"safe" | "confirm" | "deny"` or a function of the validated args returning an `Assessment` (see `tools/shell.py`, `tools/files.py`). Register new tools in `tools/__init__.py:default_tools`.
- Tools raise `ToolError` for expected failures (message goes to the model verbatim). System commands go through `tools/_proc.py` (`run` waits, `launch` detaches for apps), which turns a missing binary into an apt hint. Tests monkeypatch `_proc.run` / `_proc.launch`.
- Shell policy lives in `tools/safety.py:assess_shell`: deny patterns first, then anything with shell operators or secret paths needs confirmation, and only a small read-only allowlist runs without asking.
- `session.py:start()` builds a running Noah (agent, registry, `TaskManager`, `Announcer`) and loads unfinished checklists. `noah chat`, `noah say` and the voice daemon all start there; they differ only in the `confirm` and `say` callbacks.
- Checklists (`tasks/`): `TaskStore` (SQLite, `PRAGMA user_version` migrations) → `TaskManager` (keeps open tasks in memory, emits `TaskEvent`s to async subscribers, `gate(confirm)` marks the active item `waiting` while asking) → `Announcer` (template lines, grouping, verbosity, `GdbusNotifier`). The agent's checklist tools are built per manager in `tools/checklist.py`; `default_tools()` stays stateless.
- `chat.py:Renderer` prints events and announcer lines for `noah chat` / `noah say`, hiding checklist tool calls.
- Voice (`noah run`, built in `voice.py:run_voice`): `audio/input.py:Microphone` (one 16 kHz stream, a queue per subscriber) → `orchestrator.py:Orchestrator`, a single event loop over mic frames and hotkey presses with states `idle` / `listening` / `busy`. It owns the wake word (`audio/wakeword.py`), VAD endpointing (`audio/vad.py`), Whisper (`stt/faster_whisper.py`, profile from `power.py`), spoken confirmation (`Orchestrator.confirm`), barge-in, follow-up, held announcements, do-not-disturb and background checklists. `speech.py:Speaker` splits streamed text into sentences and speaks them through Piper (`tts/piper.py`) and `audio/player.py`. Each turn's timings and RAM go to `timing.log` (`metrics.py`).
- Other tool factories that need state: `controls.py` (verbosity, DND) and `tools/web.py` (search via OpenRouter's web plugin). All are added in `session.start()`.
- `scripts/eval_models.py` benchmarks models through the real loop with simulated tools (see `docs/model-eval.md`). It costs real OpenRouter credit.
- Tests use `tests/fakes.py`: `FakeLLM` replays scripted responses and records what it was sent; `FakeTools` records executions.

## Conventions

- Target machine: Pop!_OS 22.04, GNOME on **X11**, Ryzen 5 4600H, 14 GB RAM (often only ~5 GB free), GTX 1650 4 GB. Noah's process must stay under ~1 GB RAM; never add a local LLM.
- Text first, voice last: the agent, tools and checklists must work in `noah chat` before audio touches them. The orchestrator is tested by feeding it frames directly (`tests/test_voice.py`); never open the real mic in tests.
- Anything that touches the system (subprocesses, `/sys`, env, hardware) is passed in as an argument with a real default, so tests can fake it. See `doctor.py` and `load_settings(env=...)`.
- Config: `config.py` loads `~/.config/noah/config.toml` (or `$NOAH_CONFIG`) into pydantic models with `extra="forbid"`; secrets only come from the environment or `.env` files, never the TOML.
- Every tool will declare a risk level (`safe` / `confirm` / `deny`) and go through the safety gate. Don't add a path that runs OS actions around it.
