"""Install and switch fork builds of Pandrator in a Manager-style install.

Run with: uv run --no-project python fork_slot.py --workspace <dir> <command>

Commands: install <tag>, activate <sha-or-tag>, back, status.
Standard library only. The Manager is never touched; only the install's
``app/versions`` slots, ``app/current*.json`` pointers and ``data/backups``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

WARNING = (
    "WARNING: do not use the Manager's Update or Repair on the app. They re-clone "
    "upstream and re-point app/current.json, discarding the fork build."
)
MARKERS = ("pyproject.toml", "pandrator/web/cli.py")
SLOT_NAME = re.compile(r"[A-Za-z0-9._-]{1,80}")


class Failure(Exception):
    """A refusal or failure that ends the run with exit code 1."""


class Install:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "Pandrator"
        self.app = self.root / "app"
        self.versions = self.app / "versions"
        self.pointer = self.app / "current.json"
        self.previous = self.app / "current.previous.json"
        self.data = self.root / "data"
        self.database = self.data / "pandrator.sqlite3"
        exe = "pixi.exe" if os.name == "nt" else "pixi"
        self.pixi = self.root / "bin" / exe

    def require_install(self) -> None:
        if not self.versions.is_dir():
            raise Failure(f"No install found: {self.versions} does not exist.")


# --- helpers -----------------------------------------------------------------


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, text=True, **kwargs)


def git_head(path: Path) -> str:
    result = run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True)
    if result.returncode != 0:
        raise Failure(f"{path} is not a git checkout: {result.stderr.strip()}")
    return result.stdout.strip()


def slot_tags(path: Path) -> list[str]:
    result = run(["git", "-C", str(path), "tag", "--points-at", "HEAD"], capture_output=True)
    return result.stdout.split() if result.returncode == 0 else []


def slot_version(path: Path) -> str:
    try:
        data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def check_slot(slot: Path) -> None:
    if not slot.is_dir():
        raise Failure(f"Slot {slot} does not exist.")
    for marker in MARKERS:
        if not (slot / marker).is_file():
            raise Failure(f"Slot {slot.name} lacks {marker}.")
    if (slot / "pandrator-release.json").exists():
        raise Failure(f"Slot {slot.name} has pandrator-release.json and would be a release bundle.")


def inside_versions(install: Install, raw: str) -> Path | None:
    versions = install.versions.resolve()
    candidate = Path(raw).resolve()
    return candidate if candidate.parent == versions else None


def read_pointer(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def pointer_slot(install: Install, payload: dict | None) -> Path | None:
    if not payload or not isinstance(payload.get("path"), str):
        return None
    return inside_versions(install, payload["path"])


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pointer_for(slot: Path) -> dict:
    return {
        "component_id": "pandrator",
        "version": slot.name,
        "revision": git_head(slot),
        "path": str(slot),
        "activated_by": "fork_slot",
    }


def list_slots(install: Install) -> list[Path]:
    if not install.versions.is_dir():
        return []
    return sorted(
        p for p in install.versions.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def resolve_slot(install: Install, target: str) -> Path:
    slots = list_slots(install)
    exact = [s for s in slots if s.name == target]
    if exact:
        return exact[0]
    by_tag = [s for s in slots if target in slot_tags(s)]
    if len(by_tag) == 1:
        return by_tag[0]
    if len(by_tag) > 1:
        raise Failure(f"Tag {target} matches several slots.")
    by_prefix = [s for s in slots if len(target) >= 7 and s.name.startswith(target)]
    if len(by_prefix) == 1:
        return by_prefix[0]
    raise Failure(f"No slot matches {target!r}.")


# --- running check -----------------------------------------------------------


def list_processes() -> list[tuple[int, str]]:
    """Return (pid, executable path) for processes with a known path."""
    if os.name == "nt":
        script = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
        )
        result = run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise Failure(f"Could not list processes: {result.stderr.strip()}")
        text = result.stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            return [(int(row["ProcessId"]), str(row["ExecutablePath"])) for row in data]
        except (ValueError, KeyError, TypeError) as error:
            raise Failure(f"Could not parse the process list: {error}") from error
    result = run(["ps", "-eo", "pid=,args="], capture_output=True)
    if result.returncode != 0:
        raise Failure(f"Could not list processes: {result.stderr.strip()}")
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1].split(" ")[0]))
    return rows


def running_processes(root: Path) -> list[tuple[int, str]]:
    prefix = os.path.normcase(str(root.resolve())).rstrip("\\/") + os.sep
    own = os.getpid()
    return [
        (pid, exe)
        for pid, exe in list_processes()
        if pid != own and os.path.normcase(exe).startswith(prefix)
    ]


def refuse_if_running(install: Install) -> None:
    found = running_processes(install.root)
    if found:
        listing = ", ".join(f"{pid} {exe}" for pid, exe in found[:5])
        raise Failure(f"Processes are running from {install.root}: {listing}. Stop the app first.")


# --- backups and switching ---------------------------------------------------


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def back_up(install: Install) -> None:
    ts = stamp()
    if install.pointer.is_file():
        target = install.app / f"current.json.{ts}.bak"
        shutil.copy2(install.pointer, target)
        print(f"Backed up pointer to {target}")
    if install.database.is_file():
        backups = install.data / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        target = backups / f"pandrator-{ts}.sqlite3"
        try:
            source = sqlite3.connect(install.database.as_uri() + "?mode=ro", uri=True)
            try:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
            finally:
                source.close()
        except (sqlite3.Error, OSError) as error:
            target.unlink(missing_ok=True)
            raise Failure(f"Database backup failed: {error}") from error
        print(f"Backed up database to {target}")
    else:
        print(f"Note: no database at {install.database}; nothing to back up.")


def switch(install: Install, new_pointer: dict, old_pointer: dict | None) -> None:
    """Back up, then write the previous pointer first and the current one last."""
    back_up(install)
    if old_pointer is not None:
        write_json_atomic(install.previous, old_pointer)
    else:
        install.previous.unlink(missing_ok=True)
    write_json_atomic(install.pointer, new_pointer)


# --- commands ----------------------------------------------------------------


def default_source() -> str:
    here = Path(__file__).resolve().parent
    result = run(["git", "-C", str(here), "remote", "get-url", "origin"], capture_output=True)
    url = result.stdout.strip()
    if result.returncode != 0 or not url:
        raise Failure("No --source given and the fork's origin URL could not be read.")
    return url


def cmd_install(install: Install, args: argparse.Namespace) -> None:
    install.require_install()
    source = args.source or default_source()
    install.app.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="fork-clone-", dir=install.app))
    clone = staging / "checkout"
    try:
        result = run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "clone",
                "--depth",
                "1",
                "--branch",
                args.tag,
                source,
                str(clone),
            ]
        )
        if result.returncode != 0:
            raise Failure(f"Cloning {args.tag} from {source} failed.")
        sha = git_head(clone)
        if not SLOT_NAME.fullmatch(sha):
            raise Failure(f"Unexpected revision {sha!r}.")
        slot = install.versions / sha
        if slot.exists():
            print(f"Slot {sha} exists; checking it.")
            check_slot(slot)
            if git_head(slot) != sha:
                raise Failure(f"Slot {slot.name} does not hold commit {sha}.")
            refuse_if_running(install)
        else:
            check_slot(clone)
            install.versions.mkdir(parents=True, exist_ok=True)
            os.replace(clone, slot)
            print(f"Created slot {slot}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if not install.pixi.is_file():
        raise Failure(f"{install.pixi} is missing; the slot was left for inspection.")
    result = run(
        [str(install.pixi), "install", "--locked", "--manifest-path", str(slot / "pixi.toml")],
        cwd=slot,
    )
    if result.returncode != 0:
        raise Failure(f"pixi install failed; slot {slot} left for inspection, pointer untouched.")
    print(f"Slot {sha} is ready, version {slot_version(slot)}. Pointer unchanged.")


def cmd_activate(install: Install, args: argparse.Namespace) -> None:
    install.require_install()
    slot = resolve_slot(install, args.target)
    check_slot(slot)
    refuse_if_running(install)
    old = read_pointer(install.pointer)
    if pointer_slot(install, old) == slot:
        print(f"Slot {slot.name} is already active; nothing changed.")
        return
    switch(install, pointer_for(slot), old)
    print(f"Activated {slot.name} (version {slot_version(slot)}).")


def cmd_back(install: Install, args: argparse.Namespace) -> None:
    install.require_install()
    previous = read_pointer(install.previous)
    slot = pointer_slot(install, previous)
    if previous is None or slot is None:
        raise Failure("No usable previous pointer (app/current.previous.json).")
    check_slot(slot)
    refuse_if_running(install)
    switch(install, previous, read_pointer(install.pointer))
    print(f"Switched to {slot.name} (version {slot_version(slot)}).")


def describe(install: Install, label: str, payload: dict | None) -> None:
    slot = pointer_slot(install, payload)
    if slot is None:
        print(f"{label}: none")
    else:
        print(f"{label}: {slot.name} (version {slot_version(slot)})")


def cmd_status(install: Install, args: argparse.Namespace) -> None:
    describe(install, "Active", read_pointer(install.pointer))
    describe(install, "Previous", read_pointer(install.previous))
    found = running_processes(install.root) if install.root.is_dir() else []
    if found:
        pids = ", ".join(str(pid) for pid, _ in found[:5])
        print(f"Processes running from the install: yes (pids {pids})")
    else:
        print("Processes running from the install: no")
    print("Slots:")
    for slot in list_slots(install):
        tags = ",".join(slot_tags(slot))
        print(f"  {slot.name}  version {slot_version(slot)}" + (f"  tags {tags}" if tags else ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace",
        default=os.environ.get("PANDRATOR_WORKSPACE"),
        help="Directory containing the Pandrator folder (or set PANDRATOR_WORKSPACE).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="Clone a tag into a new slot and prepare its runtime.")
    p.add_argument("tag")
    p.add_argument("--source", help="Repository URL or path (default: this fork's origin).")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("activate", help="Point the install at a slot.")
    p.add_argument("target", help="Slot sha (or unique prefix) or tag.")
    p.set_defaults(func=cmd_activate)
    p = sub.add_parser("back", help="Swap with the previous pointer; again goes forward.")
    p.set_defaults(func=cmd_back)
    p = sub.add_parser("status", help="Show active and previous slots and running state.")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    print(WARNING)
    args = build_parser().parse_args(argv)
    try:
        if not args.workspace:
            raise Failure("Give --workspace or set PANDRATOR_WORKSPACE.")
        args.func(Install(Path(args.workspace)), args)
    except Failure as failure:
        print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
