# Noah TODO

Where the build stands, and what to do next. Read this first when picking the project up in a new session, after `CLAUDE.md`, [architecture.md](architecture.md) and [build-plan.md](build-plan.md).

Last updated: 2026-10-14, at the end of the M3–M8 build session.

## Status

| Milestone | Code | "Done when" check | Branch |
|---|---|---|---|
| M0 Skeleton | Done | Passed | merged |
| M1 Agent loop | Done | Passed | merged |
| M2 Tools and safety | Done | Passed | merged |
| M3 Checklists | Done | **Passed** live: the test-noah request, notifications, `noah tasks` after restart | `m3-checklists` |
| M4 Model choice | Done (`scripts/eval_models.py`) | **Partial:** the account ran out of free credit partway through. Provisional pick made | `m4-m7-model-and-voice` |
| M5 Speaks | Done | Piper: 34 ms for a short sentence (target 150 ms). **You:** listen and judge the voice | same |
| M6 Push-to-talk | Done | Whisper timed on the GPU and CPU. **You:** hold Right Ctrl and talk | same |
| M7 Hands-free | Done | Wake word fires on synthetic "Hey Jarvis". Idle CPU about 6% (target 5%). **You:** an hour of use, false wake-ups, fan noise | same |
| M8 Daily driver | Done | **You:** `noah service install`, then a week of use | `m8-daily-driver` |
| M9 Extend | Not started | Chosen from the week of use | |

The branches are stacked: `m3-checklists` → `m4-m7-model-and-voice` → `m8-daily-driver`. Each has a local commit and nothing is pushed. Merge them in order, or squash them into one PR.

## What needs you

1. **Add OpenRouter credit** (a few dollars), then finish M4: `uv run python scripts/eval_models.py --out results.json`, and update `docs/model-eval.md` and the default in `config.py`/`config.example.toml` if the ranking changes.
2. **Try the voice:** `uv run noah chat --speak`, then `uv run noah run --ptt` (hold Right Ctrl), then `uv run noah run` ("Hey Jarvis, ..."). Watch `~/.local/state/noah/timing.log` for latency and RAM.
3. **Start at login:** `uv run noah service install`, and check `journalctl --user -u noah -f`. It isn't installed yet, because it would start listening on the mic.
4. **Custom wake word:** train "Hey Noah" with [wake-word.md](wake-word.md). It's a Colab job, about an hour.
5. **Daily use for a week**, then list what broke or was missing. That list picks M9.

## How we work

- The user pushes and merges. Commits are local, one per milestone branch.
- A milestone is done when its "done when" check in `build-plan.md` passes and `ruff format`, `ruff check`, `pyright` and `pytest` all pass.
- A live check against OpenRouter is fine when there's credit. Don't run actions that visibly change the user's desktop, or open the mic, without asking.
- `uv` is in `~/.local/bin`. If it's not on `PATH`, run `source ~/.local/bin/env`.

## Decisions made

Not spelled out in `architecture.md`:

- **Model:** `anthropic/claude-haiku-5.5` with fallback `deepseek/deepseek-v4.1-flash` (OpenRouter's `models` list). Provisional, from the partial M4 run. `max_tokens = 2048`. Without it, OpenRouter reserves credit for 65k output tokens, and that alone gave 402s.
- **Privacy:** `provider.data_collection = "deny"` is sent by default (`[llm] deny_data_collection`).
- **Messages:** plain OpenAI-format dicts. The system prompt is rebuilt every call, includes open checklists, and is never stored in history.
- **Step limit:** `max_steps` (8), or `max_steps_with_checklist` (40) while a checklist is open. It only rises within a turn.
- **Checklist item ids** are 1-based numbers within a task.
- **Confirmation inside a checklist:** `TaskManager.gate` marks the in-progress item `waiting` (`asking=True`) while asking, then sets it back to `in_progress`. The announcer flushes grouped progress first, but doesn't add its own "I need your OK" line, because the prompt asks.
- **Voice confirmation:** "I need your OK to …. Go ahead?", then listen up to 8 s with VAD, or hold the hotkey to answer. If the answer is unclear, ask once more, then treat it as no.
- **STT models:** `small.en` on the GPU, `base.en` on the CPU, greedy decoding. Measured; `large-v3-turbo` is too slow on a GTX 1650.
- **CUDA:** comes from the `gpu` extra (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). `power.preload_cuda_libraries()` loads them with ctypes, so no `LD_LIBRARY_PATH` is needed. If the GPU model fails to load, Noah falls back to the CPU model.
- **openWakeWord 0.4.0**, not 0.6: newer versions require `tflite-runtime`, which has no Python 3.13 wheels. 0.4 is ONNX-only and ships the Hey Jarvis model.
- **VAD:** Silero v6 ONNX from faster-whisper's assets, run frame by frame (`audio/vad.py`). Speech ends after 700 ms of silence; the wait for speech to start is 5 s.
- **Mic:** 96 ms blocks split into 32 ms frames. Every subscriber gets its own bounded queue.
- **Barge-in** is the wake word or the hotkey, not VAD. Without echo cancellation, VAD would hear Noah's own voice. The follow-up window opens 300 ms after Noah stops, for the same reason.
- **Background checklists:** if you interrupt a turn while a checklist is open, that turn keeps running (its final reply comes back as an announcement), and new requests go to a second agent (`Noah.side_agent()`). `cancel_task` on that checklist cancels the background turn.
- **Web search:** OpenRouter's `web` plugin with the main model, so there's no second API key.
- **Screenshots:** `ffmpeg -f x11grab`, because `gnome-screenshot` isn't installed.
- **Writing files:** `write_file` is safe for a new file and `confirm` to overwrite or append. `move_file` is always `confirm`. `make_folder` is safe. All are limited to home, and secret paths are denied.
- **Wake word:** Hey Jarvis until a custom model is trained ([wake-word.md](wake-word.md)).
- **Tests:** the openai SDK (3.x) uses `httpx2`, so tests that build SDK errors import `httpx2`.

## Measured on the laptop

| What | Result | Target |
|---|---|---|
| Piper, a short sentence | 34 ms (168 ms for a 4.6 s sentence) | under 150 ms to the first audio |
| Whisper `small.en`, GPU | 0.47 s per command | 200–400 ms |
| Whisper `base.en`, CPU | 0.55 s per command | 600–900 ms |
| RAM, everything loaded (GPU profile) | about 1,010 MB | under 1 GB (the 1.5 GB machine budget is fine) |
| Idle CPU, wake word only | about 5.6–6% of one core before the silence gate; the model itself is 3.5% and now rests in a quiet room | under 5% |
| LLM first token (Haiku 5.5) | about 1.6–1.8 s | 0.3–0.7 s |

The first LLM token is now the biggest part of the latency. Try `reasoning_effort = "minimal"`, or a faster model once M4 finishes.

## Known issues and follow-ups

- [x] **Idle CPU was slightly over target.** The wake word model (3.5% of a core) now rests while the mic is below `wakeword_gate_dbfs` (-50), and replays the last 2 s when sound returns. A synthetic "Hey Jarvis" after 5 s of quiet fires at the same moment with the gate on. **You:** re-measure idle CPU with the real mic; if a quiet "Hey Jarvis" gets missed, lower the gate (`noah run -v` logs the level).
- [ ] **Whisper mangles names like "test-noah"** ("testnoincodes"). The initial prompt helps a little. Consider adding folder names from `~/codes` to the prompt.
- [ ] **The screenshot tool saved a nearly empty image** in testing. The screen was probably locked or off, but it hasn't been checked while you're at the desk.
- [x] **Declining without a terminal read as the user saying no.** A `Confirm` callback now raises `CannotAsk`, and the registry tells the model (and the audit log, `"unconfirmed"`) that it couldn't ask. Checked live with piped `noah say`.
- [ ] **No echo cancellation.** If you use speakers rather than headphones, consider PipeWire's `echo-cancel` module before turning on VAD-based barge-in.
- [x] **Abandoned checklists raised the step limit for every request.** Only checklists touched in the last `checklist_active_hours` (12) count now. Old ones stay listed until you cancel them.
- [ ] **Preamble before tool calls.** The prompt now says not to announce tool calls. Check how often models still do it out loud.
- [ ] **No `platform/` layer.** Tools call Linux commands directly. Add it only when a second OS is targeted.
- [ ] **The architecture diagrams page** (https://claude.ai/artifact/RScKGkpPUc6rnBrfwf49ZN) predates the voice pipeline.

## M9 candidates

The week of daily use should decide. The candidates from the plan are: an MCP client (load tools from MCP servers, with `confirm` as the default risk), long-term memory, routing between a fast and a smart model, a tray indicator showing the state and DND, and more apps.
