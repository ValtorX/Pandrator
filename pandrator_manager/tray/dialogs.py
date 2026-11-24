"""Native yes/no and message dialogs for the tray, with no extra dependency.

Windows uses ``MessageBoxW`` through ctypes, macOS uses ``osascript`` and Linux
uses ``zenity`` or ``kdialog``. The answer is always "No" when no dialog can be
shown, so a missing tool never accepts a destructive question.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping

from ..desktop import host_process_environment
from ..workspace_selection import normalized_system

Runner = Callable[[list[str], Mapping[str, str]], "subprocess.CompletedProcess[str]"]
MessageBox = Callable[[str, str, int], int]
Which = Callable[[str], str | None]

_MB_OK = 0x00000000
_MB_YESNO = 0x00000004
_MB_ICONQUESTION = 0x00000020
_MB_ICONINFORMATION = 0x00000040
_MB_DEFBUTTON2 = 0x00000100
_MB_SETFOREGROUND = 0x00010000
_MB_TOPMOST = 0x00040000
_ID_YES = 6


def _run(command: list[str], environ: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=host_process_environment(environ),
        check=False,
    )


def _windows_message_box(title: str, message: str, flags: int) -> int:
    import ctypes
    from ctypes import wintypes

    # A private handle, so the argtypes below do not change the shared one.
    user32 = ctypes.WinDLL("user32")
    user32.MessageBoxW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    user32.MessageBoxW.restype = ctypes.c_int
    return int(user32.MessageBoxW(None, message, title, flags))


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\n", '" & return & "') + '"'


def _linux_command(
    kind: str,
    title: str,
    message: str,
    *,
    which: Which,
) -> list[str] | None:
    if kind == "question":
        # Only zenity can make No the default button; kdialog's Yes/No dialog
        # defaults to Yes, so without zenity the answer is No.
        executable = which("zenity")
        if not executable:
            return None
        return [
            executable,
            "--question",
            f"--title={title}",
            f"--text={message}",
            "--no-markup",
            "--default-cancel",
        ]
    if executable := which("zenity"):
        return [
            executable,
            "--info",
            f"--title={title}",
            f"--text={message}",
            "--no-markup",
        ]
    if executable := which("kdialog"):
        return [executable, "--title", title, "--msgbox", message]
    return None


def _macos_command(kind: str, title: str, message: str, *, which: Which) -> list[str] | None:
    executable = which("osascript")
    if not executable:
        return None
    text = _applescript_string(message)
    caption = _applescript_string(title)
    if kind == "question":
        script = (
            f"display dialog {text} with title {caption} "
            'buttons {"No", "Yes"} default button "No" cancel button "No"'
        )
    else:
        script = (
            f"display dialog {text} with title {caption} "
            'buttons {"OK"} default button "OK"'
        )
    return [executable, "-e", script]


def _desktop_available(system: str, environ: Mapping[str, str]) -> bool:
    if system == "linux":
        return bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    return True


def ask_yes_no(
    title: str,
    message: str,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Which = shutil.which,
    runner: Runner = _run,
    message_box: MessageBox = _windows_message_box,
) -> bool:
    """Ask a Yes/No question defaulting to No; return True only for Yes."""

    selected_system = normalized_system(system)
    selected_environ = os.environ if environ is None else environ
    try:
        if selected_system == "windows":
            flags = (
                _MB_YESNO
                | _MB_ICONQUESTION
                | _MB_DEFBUTTON2
                | _MB_SETFOREGROUND
                | _MB_TOPMOST
            )
            return message_box(title, message, flags) == _ID_YES
        if not _desktop_available(selected_system, selected_environ):
            return False
        if selected_system == "macos":
            command = _macos_command("question", title, message, which=which)
        elif selected_system == "linux":
            command = _linux_command("question", title, message, which=which)
        else:
            command = None
        if command is None:
            logging.warning("No native dialog is available; answering No.")
            show_message(
                title,
                message
                + "\n\nThis desktop has no dialog that can ask safely, so the answer "
                "is No and nothing was stopped. Stop the work in Pandrator first, "
                "or install zenity.",
                system=system,
                environ=environ,
                which=which,
                runner=runner,
                message_box=message_box,
            )
            return False
        result = runner(command, selected_environ)
    except Exception:
        logging.exception("Could not show the desktop dialog; answering No.")
        return False
    if selected_system == "macos":
        return result.returncode == 0 and "button returned:Yes" in result.stdout
    return result.returncode == 0


def show_message(
    title: str,
    message: str,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Which = shutil.which,
    runner: Runner = _run,
    message_box: MessageBox = _windows_message_box,
) -> None:
    """Show an informational message; log it when no dialog is available."""

    selected_system = normalized_system(system)
    selected_environ = os.environ if environ is None else environ
    try:
        if selected_system == "windows":
            message_box(
                title,
                message,
                _MB_OK | _MB_ICONINFORMATION | _MB_SETFOREGROUND | _MB_TOPMOST,
            )
            return
        command = None
        if _desktop_available(selected_system, selected_environ):
            if selected_system == "macos":
                command = _macos_command("message", title, message, which=which)
            elif selected_system == "linux":
                command = _linux_command("message", title, message, which=which)
        if command is None:
            logging.warning("%s: %s", title, message)
            return
        result = runner(command, selected_environ)
        if result.returncode != 0:
            logging.warning("%s: %s", title, message)
    except Exception:
        logging.exception("Could not show the desktop message: %s", message)
