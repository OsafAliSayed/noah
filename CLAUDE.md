# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Noah is a voice-controlled assistant for the local OS (Jarvis style): wake word → local speech-to-text → LLM via OpenRouter with native tool calling → local tools that act on the OS → local text-to-speech. Multi-step requests become persisted checklists that Noah reports progress on.

- `docs/architecture.md` is the design. Read it before adding a component.
- `docs/build-plan.md` is the milestone plan (M0–M9), each with a "done when" check. Work happens one milestone per branch (`m0-skeleton`, ...).
- `docs/todo.md` is the handoff: current status, decisions made, known issues and notes for the next milestone. Read it at the start of a session and update it at the end of each milestone.

## Commands

```bash
uv sync                                   # install deps (uv lives in ~/.local/bin)
uv run noah doctor                        # check system deps, API key, mic, GPU, power
uv run pytest                             # all tests (offline, no mic needed)
uv run pytest tests/test_config.py::test_defaults_when_no_config_file   # one test
uv run ruff format src tests && uv run ruff check src tests
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
- `chat.py` renders events for `noah chat` / `noah say`. The voice daemon will consume the same events.
- Tests use `tests/fakes.py`: `FakeLLM` replays scripted responses and records what it was sent; `FakeTools` records executions.

## Conventions

- Target machine: Pop!_OS 22.04, GNOME on **X11**, Ryzen 5 4600H, 14 GB RAM (often only ~5 GB free), GTX 1650 4 GB. Noah's process must stay under ~1 GB RAM; never add a local LLM.
- Text first, voice last: the agent, tools and checklists must work in `noah chat` before audio touches them.
- Anything that touches the system (subprocesses, `/sys`, env, hardware) is passed in as an argument with a real default, so tests can fake it. See `doctor.py` and `load_settings(env=...)`.
- Config: `config.py` loads `~/.config/noah/config.toml` (or `$NOAH_CONFIG`) into pydantic models with `extra="forbid"`; secrets only come from the environment or `.env` files, never the TOML.
- Every tool will declare a risk level (`safe` / `confirm` / `deny`) and go through the safety gate. Don't add a path that runs OS actions around it.
