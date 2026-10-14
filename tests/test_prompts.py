from datetime import UTC, datetime
from pathlib import Path

from noah.agent.prompts import Context, build_system_prompt, gather_context


def test_prompt_includes_live_context():
    context = Context(
        now=datetime(2026, 10, 14, 9, 30, tzinfo=UTC),
        user="noah",
        home=Path("/home/noah"),
        os_name="Pop!_OS 22.04 LTS",
        desktop="pop:GNOME on x11",
        power="on battery",
    )
    prompt = build_system_prompt(context)
    assert "Wednesday 14 October 2026, 09:30 UTC" in prompt
    assert "noah (home: /home/noah)" in prompt
    assert "Pop!_OS 22.04 LTS, pop:GNOME on x11" in prompt
    assert "Power: on battery" in prompt


def test_gather_context_reads_session_from_env():
    context = gather_context({"XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "x11"})
    assert context.desktop == "GNOME on x11"
    assert context.now.tzinfo is not None
