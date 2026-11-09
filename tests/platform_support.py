"""Helpers for tests whose fixtures depend on the host platform."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WINDOWS_PRIVILEGE_ERROR = 1314
SYMLINK_PRIVILEGE_REASON = (
    "Creating symlinks on Windows needs Developer Mode or the symlink privilege (WinError 1314)"
)


def symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """Create a symlink, skipping the test only when Windows refuses the privilege."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if getattr(error, "winerror", None) != WINDOWS_PRIVILEGE_ERROR:
            raise
        pytest.skip(SYMLINK_PRIVILEGE_REASON)


def direct_python() -> str:
    """Return an interpreter whose process id is the script's own.

    A Windows venv ``python.exe`` is a launcher that starts the base interpreter as a child,
    so the launcher's pid differs from the script's. ``sys._base_executable`` is the same
    attribute ``venv`` and ``multiprocessing`` read.
    """
    if sys.platform == "win32" and sys.prefix != sys.base_prefix:
        return sys._base_executable
    return sys.executable
