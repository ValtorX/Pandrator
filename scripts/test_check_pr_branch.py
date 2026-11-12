"""Tests for check_pr_branch.py against a temporary repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_pr_branch  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "pyproject.toml").write_text('name = "x"\nversion = "1.0.0"\n', encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "pr")
    return tmp_path


def commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change")


def run(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = check_pr_branch.main(["main", "--repo", str(repo)])
    return code, capsys.readouterr().out


def test_clean_branch_reports_nothing(repo: Path, capsys) -> None:
    commit(repo, {"pandrator/fix.py": "x = 1\n"})
    assert run(repo, capsys) == (0, "")


@pytest.mark.parametrize(
    "path",
    [
        "_bmad-output/notes.md",
        "_bmad/config.toml",
        ".agents/skills/a.md",
        ".claude/settings.json",
        "CLAUDE.md",
        "AGENTS.md",
        "skills-lock.json",
        "_bmad-custom/x",
        "web/AGENTS.md",
        "pandrator/CLAUDE.md",
    ],
)
def test_fork_only_path_is_named(repo: Path, capsys, path: str) -> None:
    commit(repo, {path: "x\n"})
    code, out = run(repo, capsys)
    assert code == 1
    assert f"{path}: fork-only path" in out


def test_release_notes_edit_is_named(repo: Path, capsys) -> None:
    commit(repo, {"docs/release-notes-next.md": "- A fix.\n"})
    code, out = run(repo, capsys)
    assert code == 1
    assert "docs/release-notes-next.md: upstream's maintainer writes this file" in out


def test_fork_version_names_file_and_line(repo: Path, capsys) -> None:
    commit(repo, {"pyproject.toml": 'name = "x"\nversion = "1.0.0+fork.1"\n'})
    code, out = run(repo, capsys)
    assert code == 1
    assert "pyproject.toml:2: added line contains +fork" in out


def test_windows_user_path_names_file_and_line(repo: Path, capsys) -> None:
    commit(repo, {"pandrator/a.py": "ok = 1\np = 'C:\\Users\\someone\\x'\n"})
    code, out = run(repo, capsys)
    assert code == 1
    assert "pandrator/a.py:2: added line contains a Windows user path" in out


def test_removed_marker_is_not_a_finding(repo: Path, capsys) -> None:
    commit(repo, {"pyproject.toml": 'name = "x"\nversion = "1.0.0+fork.1"\n'})
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--ff-only", "pr")
    git(repo, "checkout", "-q", "-b", "pr2")
    commit(repo, {"pyproject.toml": 'name = "x"\nversion = "1.0.1"\n'})
    assert run(repo, capsys) == (0, "")


def test_added_line_starting_with_plusplus_is_not_a_header(repo: Path, capsys) -> None:
    commit(repo, {"pandrator/a.py": "++ x\nversion = '1+fork.1'\n"})
    code, out = run(repo, capsys)
    assert code == 1
    assert "pandrator/a.py:2: added line contains +fork" in out


def test_noprefix_config_does_not_hide_findings(repo: Path, capsys) -> None:
    git(repo, "config", "diff.noprefix", "true")
    commit(repo, {"pyproject.toml": 'name = "x"\nversion = "1.0.0+fork.1"\n'})
    code, out = run(repo, capsys)
    assert code == 1
    assert "pyproject.toml:2: added line contains +fork" in out


def test_missing_base_ref_exits_2(repo: Path, capsys) -> None:
    code = check_pr_branch.main(["nope/main", "--repo", str(repo)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "could not run" in captured.err
