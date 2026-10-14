"""Noah's tools. Every tool runs through ToolRegistry, which applies the safety gate."""

from noah.tools.apps import open_app, open_path_or_url
from noah.tools.desktop import get_time, get_volume, notify, set_volume, system_info
from noah.tools.files import list_files, read_file
from noah.tools.registry import Tool
from noah.tools.shell import run_shell


def default_tools() -> list[Tool]:
    return [
        get_time,
        system_info,
        open_app,
        open_path_or_url,
        get_volume,
        set_volume,
        notify,
        list_files,
        read_file,
        run_shell,
    ]
