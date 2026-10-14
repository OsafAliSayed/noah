from pathlib import Path

from noah import doctor
from noah.config import load_settings


def test_api_key_missing_fails(tmp_path: Path):
    settings = load_settings(config_path=tmp_path / "none.toml", env={})
    check = doctor.check_api_key(settings)
    assert check.status == "fail"
    assert check.hint and "openrouter.ai/keys" in check.hint


def test_api_key_present_ok(tmp_path: Path):
    settings = load_settings(config_path=tmp_path / "none.toml", env={"OPENROUTER_API_KEY": "k"})
    assert doctor.check_api_key(settings).status == "ok"


def test_bad_config_fails():
    check = doctor.check_config(None, "config.toml: bad")
    assert check.status == "fail"
    assert "bad" in check.detail


def test_missing_command_warns_with_apt_hint():
    checks = doctor.check_commands(which=lambda cmd: None if cmd == "playerctl" else "/usr/bin/x")
    by_name = {c.name: c for c in checks}
    assert by_name["playerctl"].status == "warn"
    assert by_name["playerctl"].hint == "sudo apt install playerctl"
    assert by_name["xclip"].status == "ok"


def test_mic_reads_default_source():
    output = "Server Name: PulseAudio (on PipeWire 1.0.3)\nDefault Source: alsa_input.analog\n"
    check = doctor.check_mic(run=lambda _: output)
    assert check.status == "ok"
    assert check.detail == "alsa_input.analog"


def test_mic_warns_when_pactl_missing():
    assert doctor.check_mic(run=lambda _: None).status == "warn"


def test_gpu_parses_nvidia_smi():
    check = doctor.check_gpu(run=lambda _: "NVIDIA GeForce GTX 1650, 4096 MiB\n")
    assert check.status == "ok"
    assert "GTX 1650, 4096 MiB" in check.detail


def test_no_gpu_warns():
    assert doctor.check_gpu(run=lambda _: None).status == "warn"


def _supply(root: Path, name: str, kind: str, online: str | None = None) -> None:
    (root / name).mkdir()
    (root / name / "type").write_text(f"{kind}\n")
    if online is not None:
        (root / name / "online").write_text(f"{online}\n")


def test_power_on_charger(tmp_path: Path):
    _supply(tmp_path, "BAT0", "Battery")
    _supply(tmp_path, "ACAD", "Mains", "1")
    check = doctor.check_power(tmp_path)
    assert check.status == "ok"
    assert check.detail == "on charger (ACAD)"


def test_power_on_battery(tmp_path: Path):
    _supply(tmp_path, "ACAD", "Mains", "0")
    assert doctor.check_power(tmp_path).detail == "on battery (ACAD)"


def test_power_no_charger_warns(tmp_path: Path):
    assert doctor.check_power(tmp_path).status == "warn"


def test_session_x11_ok_wayland_warns():
    assert doctor.check_session({"XDG_SESSION_TYPE": "x11"}).status == "ok"
    assert doctor.check_session({"XDG_SESSION_TYPE": "wayland"}).status == "warn"


def test_low_memory_warns():
    assert doctor.check_memory(available=lambda: 500_000_000).status == "warn"
    assert doctor.check_memory(available=lambda: 4_000_000_000).status == "ok"
