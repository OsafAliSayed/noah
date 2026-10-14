"""Noah's tools. Every tool runs through ToolRegistry, which applies the safety gate."""

from noah.tools.apps import open_app, open_path_or_url
from noah.tools.desktop import get_time, get_volume, notify, set_volume, system_info
from noah.tools.files import list_files, read_file
from noah.tools.registry import Tool
from noah.tools.shell import run_shell
from noah.tools.windows import (
    clipboard_get,
    clipboard_set,
    focus_window,
    get_brightness,
    list_windows,
    media_control,
    media_status,
    screenshot,
    set_brightness,
)
from noah.tools.write import make_folder, move_file, write_file


def default_tools() -> list[Tool]:
    return [
        get_time,
        system_info,
        open_app,
        open_path_or_url,
        get_volume,
        set_volume,
        get_brightness,
        set_brightness,
        media_control,
        media_status,
        notify,
        list_windows,
        focus_window,
        clipboard_get,
        clipboard_set,
        screenshot,
        list_files,
        read_file,
        make_folder,
        write_file,
        move_file,
        run_shell,
    ]
