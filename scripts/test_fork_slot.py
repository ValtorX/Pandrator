"""Tests for fork_slot.py against a temporary install and a local bare repo."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fork_slot  # noqa: E402

TAG = "v.0.14.2+fork.1"


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str]:
    work = tmp_path / "work"
    (work / "pandrator" / "web").mkdir(parents=True)
    work.joinpath("pyproject.toml").write_text(
        '[project]\nname = "pandrator"\nversion = "0.14.2+fork.1"\n', encoding="utf-8"
    )
    work.joinpath("pixi.toml").write_text("[project]\n", encoding="utf-8")
    work.joinpath("pandrator", "web", "cli.py").write_text("", encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=work)
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "fork", cwd=work)
    git("tag", TAG, cwd=work)
    bare = tmp_path / "bare.git"
    git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return bare, git("rev-parse", "HEAD", cwd=work)


def make_old_slot(root: Path, name: str = "27a4479a") -> Path:
    slot = root / "app" / "versions" / name
    (slot / "pandrator" / "web").mkdir(parents=True)
    slot.joinpath("pyproject.toml").write_text(
        '[project]\nname = "pandrator"\nversion = "0.14.1"\n', encoding="utf-8"
    )
    slot.joinpath("pandrator", "web", "cli.py").write_text("", encoding="utf-8")
    git("init", "-q", cwd=slot)
    git("add", ".", cwd=slot)
    git("commit", "-q", "-m", "old", cwd=slot)
    return slot


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "ws"
    root = workspace / "Pandrator"
    old = make_old_slot(root)
    (root / "bin").mkdir()
    (root / "bin" / ("pixi.exe" if fork_slot.os.name == "nt" else "pixi")).write_text("")
    (root / "data").mkdir()
    with sqlite3.connect(root / "data" / "pandrator.sqlite3") as db:
        db.execute("create table t (v text)")
        db.execute("insert into t values ('kept')")
    fork_slot.write_json_atomic(
        root / "app" / "current.json",
        {"component_id": "pandrator", "version": "27a4479a", "path": str(old)},
    )
    monkeypatch.setattr(fork_slot, "list_processes", lambda: [])
    return workspace


class PixiStub:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[Path] = []
        self.rc = 0


@pytest.fixture
def pixi_calls(monkeypatch: pytest.MonkeyPatch) -> PixiStub:
    stub = PixiStub()
    real = fork_slot.run

    def fake(command, **kwargs):
        if Path(command[0]).stem == "pixi":
            stub.calls.append(command)
            stub.cwds.append(Path(kwargs["cwd"]))
            return subprocess.CompletedProcess(command, stub.rc, "", "")
        return real(command, **kwargs)

    monkeypatch.setattr(fork_slot, "run", fake)
    return stub


def invoke(workspace: Path, *args: str) -> int:
    return fork_slot.main(["--workspace", str(workspace), *args])


def pointer(workspace: Path, name: str = "current.json") -> dict:
    return json.loads((workspace / "Pandrator" / "app" / name).read_text(encoding="utf-8"))


def test_install_creates_slot_and_leaves_pointer(install, source, pixi_calls, capsys):
    bare, sha = source
    before = pointer(install)
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 0
    slot = install / "Pandrator" / "app" / "versions" / sha
    assert (slot / "pyproject.toml").is_file()
    assert b"\r\n" not in (slot / "pyproject.toml").read_bytes()
    assert pointer(install) == before
    assert pixi_calls.calls[0][1:3] == ["install", "--locked"]
    assert pixi_calls.cwds[0] == slot
    out = capsys.readouterr().out
    assert "0.14.2+fork.1" in out and "Update or Repair" in out
    assert list((install / "Pandrator" / "app").glob("fork-clone-*")) == []


def test_install_again_reuses_slot_and_reruns_pixi(install, source, pixi_calls):
    bare, sha = source
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 0
    marker = install / "Pandrator" / "app" / "versions" / sha / "marker.txt"
    marker.write_text("untouched")
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 0
    assert marker.read_text() == "untouched"
    assert len(pixi_calls.calls) == 2


def test_install_refuses_slot_missing_markers(install, source, pixi_calls):
    bare, sha = source
    broken = install / "Pandrator" / "app" / "versions" / sha
    broken.mkdir()
    (broken / "keep.txt").write_text("x")
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 1
    assert pixi_calls.calls == []
    assert [p.name for p in broken.iterdir()] == ["keep.txt"]


def test_install_refuses_release_bundle_slot(install, source, pixi_calls):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    slot = install / "Pandrator" / "app" / "versions" / sha
    (slot / "pandrator-release.json").write_text("{}")
    pixi_calls.calls.clear()
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 1
    assert invoke(install, "activate", sha) == 1
    assert pixi_calls.calls == []


def test_install_existing_slot_refuses_while_running(install, source, pixi_calls, monkeypatch):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    pixi_calls.calls.clear()
    root = (install / "Pandrator").resolve()
    monkeypatch.setattr(fork_slot, "list_processes", lambda: [(7, str(root / "app" / "a.exe"))])
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 1
    assert pixi_calls.calls == []


def test_install_refuses_slot_with_wrong_commit(install, source, pixi_calls):
    bare, sha = source
    slot = make_old_slot(install / "Pandrator", sha)  # name is the sha, content differs
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 1
    assert slot.is_dir()


def test_pixi_failure_exits_1_and_keeps_slot(install, source, pixi_calls):
    bare, sha = source
    pixi_calls.rc = 1
    before = pointer(install)
    assert invoke(install, "install", TAG, "--source", bare.as_uri()) == 1
    assert (install / "Pandrator" / "app" / "versions" / sha).is_dir()
    assert pointer(install) == before


def test_clone_failure_exits_1(install, tmp_path, pixi_calls):
    assert invoke(install, "install", "nope", "--source", str(tmp_path / "missing")) == 1
    assert pointer(install)["version"] == "27a4479a"


def test_activate_back_and_forward(install, source, pixi_calls):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    old = pointer(install)
    assert invoke(install, "activate", TAG) == 0
    new = pointer(install)
    assert new["version"] == sha and new["revision"] == sha
    assert new["component_id"] == "pandrator" and new["activated_by"]
    assert new["path"].endswith(sha)
    assert pointer(install, "current.previous.json") == old
    app = install / "Pandrator" / "app"
    assert list(app.glob("current.json.*.bak"))
    backups = list((install / "Pandrator" / "data" / "backups").glob("*.sqlite3"))
    assert backups
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("select v from t").fetchone() == ("kept",)

    assert invoke(install, "back") == 0
    assert pointer(install) == old
    assert pointer(install, "current.previous.json") == new
    assert invoke(install, "back") == 0
    assert pointer(install) == new


def test_activate_by_prefix(install, source, pixi_calls):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    assert invoke(install, "activate", sha[:10]) == 0
    assert pointer(install)["version"] == sha


def test_activate_refuses_while_running(install, source, pixi_calls, monkeypatch):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    root = (install / "Pandrator").resolve()
    monkeypatch.setattr(fork_slot, "list_processes", lambda: [(4242, str(root / "bin" / "x.exe"))])
    before = pointer(install)
    assert invoke(install, "activate", sha) == 1
    assert pointer(install) == before
    assert not (install / "Pandrator" / "app" / "current.previous.json").exists()
    assert not list((install / "Pandrator" / "app").glob("current.json.*.bak"))


def test_activate_refuses_missing_or_incomplete_slot(install, pixi_calls):
    before = pointer(install)
    assert invoke(install, "activate", "deadbeefcafe") == 1
    bad = install / "Pandrator" / "app" / "versions" / "incomplete"
    bad.mkdir()
    assert invoke(install, "activate", "incomplete") == 1
    assert pointer(install) == before
    assert not (install / "Pandrator" / "app" / "current.previous.json").exists()


def test_back_without_previous_exits_1(install, pixi_calls):
    assert invoke(install, "back") == 1


def test_back_refuses_while_running(install, source, pixi_calls, monkeypatch):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    invoke(install, "activate", sha)
    root = (install / "Pandrator").resolve()
    monkeypatch.setattr(fork_slot, "list_processes", lambda: [(1, str(root / "a.exe"))])
    app = install / "Pandrator" / "app"
    baks = len(list(app.glob("current.json.*.bak")))
    dbs = len(list((install / "Pandrator" / "data" / "backups").glob("*.sqlite3")))
    before = pointer(install)
    assert invoke(install, "back") == 1
    assert pointer(install) == before
    assert len(list(app.glob("current.json.*.bak"))) == baks
    assert len(list((install / "Pandrator" / "data" / "backups").glob("*.sqlite3"))) == dbs


def test_status(install, source, pixi_calls, capsys):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    invoke(install, "activate", sha)
    capsys.readouterr()
    assert invoke(install, "status") == 0
    out = capsys.readouterr().out
    assert f"Active: {sha} (version 0.14.2+fork.1)" in out
    assert "Previous: 27a4479a (version 0.14.1)" in out
    assert "Processes running from the install: no" in out
    assert "27a4479a" in out.split("Slots:")[1]


def test_workspace_from_environment(install, monkeypatch, capsys):
    monkeypatch.setenv("PANDRATOR_WORKSPACE", str(install))
    parser = fork_slot.build_parser()
    assert parser.parse_args(["status"]).workspace == str(install)
    assert fork_slot.main(["status"]) == 0


def test_activate_drops_stale_previous_when_pointer_missing(install, source, pixi_calls):
    bare, sha = source
    invoke(install, "install", TAG, "--source", bare.as_uri())
    app = install / "Pandrator" / "app"
    fork_slot.write_json_atomic(app / "current.previous.json", {"path": "stale"})
    (app / "current.json").unlink()
    assert invoke(install, "activate", sha) == 0
    assert not (app / "current.previous.json").exists()
    assert invoke(install, "back") == 1
