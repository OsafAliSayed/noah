# Noah

A voice layer over your operating system, like Jarvis. Talk to your PC, and Noah does the work and keeps you updated as it goes.

Status: early development (milestone M0). See [docs/architecture.md](docs/architecture.md) for the design and [docs/build-plan.md](docs/build-plan.md) for the roadmap.

## Setup

Requires Linux (built on Pop!_OS 22.04, GNOME on X11) and [uv](https://docs.astral.sh/uv/).

```bash
sudo apt install portaudio19-dev playerctl libnotify-bin brightnessctl xdotool wmctrl
uv sync
cp .env.example .env   # then add your OpenRouter API key
uv run noah doctor     # checks what's installed and what's missing
```
