from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests import platform_support


def _raise(error: OSError):
    def symlink_to(self, target, target_is_directory=False):
        raise error

    return symlink_to


def test_symlink_or_skip_skips_on_windows_privilege_error(tmp_path, monkeypatch):
    error = OSError(1314, "A required privilege is not held by the client")
    error.winerror = 1314
    monkeypatch.setattr(Path, "symlink_to", _raise(error))
    with pytest.raises(pytest.skip.Exception) as info:
        platform_support.symlink_or_skip(tmp_path / "link", tmp_path / "target")
    assert "WinError 1314" in str(info.value)


def test_symlink_or_skip_reraises_other_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "symlink_to", _raise(PermissionError(13, "denied")))
    with pytest.raises(PermissionError):
        platform_support.symlink_or_skip(tmp_path / "link", tmp_path / "target")


def test_symlink_or_skip_reraises_other_windows_errors(tmp_path, monkeypatch):
    error = OSError(5, "Access is denied")
    error.winerror = 5
    monkeypatch.setattr(Path, "symlink_to", _raise(error))
    with pytest.raises(OSError) as info:
        platform_support.symlink_or_skip(tmp_path / "link", tmp_path / "target")
    assert info.value is error


def test_symlink_or_skip_forwards_to_symlink_creation(tmp_path, monkeypatch):
    calls = []

    def symlink_to(self, target, target_is_directory=False):
        calls.append((self, target, target_is_directory))

    monkeypatch.setattr(Path, "symlink_to", symlink_to)
    platform_support.symlink_or_skip(
        tmp_path / "link", tmp_path / "target", target_is_directory=True
    )
    assert calls == [(tmp_path / "link", tmp_path / "target", True)]


def test_symlink_or_skip_creates_link_when_allowed(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    platform_support.symlink_or_skip(link, target)
    assert link.is_symlink()


def test_direct_python_is_the_running_interpreter_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert platform_support.direct_python() == sys.executable


def test_direct_python_is_the_running_interpreter_outside_a_venv(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    assert platform_support.direct_python() == sys.executable


def test_direct_python_uses_the_base_interpreter_in_a_windows_venv(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", "venv-prefix")
    monkeypatch.setattr(sys, "base_prefix", "base-prefix")
    monkeypatch.setattr(sys, "_base_executable", "base-python.exe")
    assert platform_support.direct_python() == "base-python.exe"
