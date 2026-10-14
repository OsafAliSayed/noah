# Noah

A voice layer over your operating system, like Jarvis. Talk to your PC, and Noah does the work and keeps you updated as it goes.

"Hey Jarvis, make a folder called scraper in codes, set up a uv project in it, and open it in VS Code." Noah makes a checklist, works through it on your machine, asks before anything risky, and tells you how it's going.

- **Brain:** an LLM through OpenRouter, using native tool calling.
- **Hands:** local tools for apps, files, the shell, volume, brightness, media, windows, the clipboard, screenshots, notifications and web search. Each tool is marked `safe`, `confirm` or `deny`.
- **Ears and voice:** all local. openWakeWord, Silero VAD, faster-whisper and Piper. Audio never leaves the machine.

See [docs/architecture.md](docs/architecture.md) for the design and [docs/build-plan.md](docs/build-plan.md) for the milestones.

## Setup

Requires Linux (built on Pop!_OS 22.04, GNOME on X11) and [uv](https://docs.astral.sh/uv/).

```bash
sudo apt install portaudio19-dev playerctl libnotify-bin brightnessctl xdotool wmctrl xclip ffmpeg
uv sync                    # add --extra gpu to run speech recognition on an NVIDIA GPU
cp .env.example .env       # then add your OpenRouter API key
uv run noah doctor         # checks what's installed and what's missing
```

## Use it

```bash
uv run noah chat              # type to Noah
uv run noah chat --speak      # type, and hear the answers
uv run noah say "set the volume to 30"
uv run noah run               # voice: say "Hey Jarvis", or hold Right Ctrl and talk
uv run noah run --ptt         # voice, hotkey only
uv run noah tasks             # current and recent checklists
uv run noah service install   # start Noah at login (systemd user service)
uv run noah config init       # write a commented ~/.config/noah/config.toml
```

Things to say: "open Firefox", "turn it down a bit", "what's using my disk", "set up a Python project called scraper", "what's left?", "keep it quiet", "do not disturb", "cancel that".

Actions that change things (shell commands, overwriting or moving files) ask first: a y/n prompt in text mode, "Go ahead?" in voice mode. Some actions are never allowed, such as `sudo`, wiping disks, or reading `~/.ssh` and `.env` files. Every action is logged to `~/.local/state/noah/actions.log`, and per-turn timings and RAM go to `timing.log` next to it.

To use your own wake word, see [docs/wake-word.md](docs/wake-word.md).
