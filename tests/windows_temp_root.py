"""Short per-run pytest temp roots for Windows without long-path support."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT_NAME = "pt"
MARKER_NAME = ".pandrator-pytest-root"
MAX_AGE_SECONDS = 24 * 60 * 60


class ShortRootUnavailable(Exception):
    """The short root cannot be used; the caller falls back to pytest's default."""


def short_root_path(temp_dir: str | Path) -> Path:
    anchor = Path(temp_dir).anchor
    if not anchor:
        raise ShortRootUnavailable(f"temp directory {temp_dir} has no drive root")
    return Path(anchor) / ROOT_NAME


def prepare_root(root: Path, now: float, max_age: float = MAX_AGE_SECONDS) -> None:
    """Create and mark the root, or accept a marked one, then prune stale runs."""
    marker = root / MARKER_NAME
    try:
        # exist_ok: a concurrent lane may create the root first.
        root.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            # An empty unmarked root is ours (a racing or interrupted run); anything else is foreign.
            if any(child.name != MARKER_NAME for child in root.iterdir()):
                raise ShortRootUnavailable(f"{root} exists and was not created by pytest here")
            marker.write_text("Temp roots for pytest runs; stale runs are pruned.\n")
    except OSError as error:
        raise ShortRootUnavailable(f"cannot create {root}: {error}") from error
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir() and now - child.stat().st_mtime > max_age:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def make_run_dir(
    platform: str | None = None,
    temp_dir: str | Path | None = None,
    clock: Callable[[], float] = time.time,
    explicit_basetemp: object = None,
) -> Path | None:
    """Return a fresh short run directory, or None when pytest's default applies.

    Raises ShortRootUnavailable when an override was wanted but could not be made.
    """
    if (platform or sys.platform) != "win32" or explicit_basetemp is not None:
        return None
    root = short_root_path(temp_dir if temp_dir is not None else tempfile.gettempdir())
    prepare_root(root, clock())
    try:
        return Path(tempfile.mkdtemp(prefix="", dir=root))
    except OSError as error:
        raise ShortRootUnavailable(f"cannot create a run directory in {root}: {error}") from error
