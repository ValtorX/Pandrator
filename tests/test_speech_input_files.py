from __future__ import annotations

import hashlib
import io
import os
import stat
import wave
from pathlib import Path

import pytest

from pandrator.runtime import DataPaths
from pandrator.web.speech_input_contracts import SpeechInputError
from pandrator.web.speech_input_files import (
    cleanup_files,
    create_staging_directory,
    file_identity,
    read_verified_file,
    stage_file,
    validate_wav,
)
from tests.platform_support import symlink_or_skip


def wav_bytes(value: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes([value, 0]) * 400)
    return output.getvalue()


def test_bounded_actual_hash_and_identity(tmp_path: Path) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    path = paths.uploads / "source.wav"
    data = wav_bytes()
    path.write_bytes(data)
    identity = file_identity(paths, "uploads/source.wav")
    actual, digest, observed = read_verified_file(
        paths, "uploads/source.wav", limit=len(data), expected_identity=identity
    )
    assert actual == data and observed == identity
    assert digest == hashlib.sha256(data).hexdigest()
    validate_wav(data)
    with pytest.raises(SpeechInputError):
        read_verified_file(paths, "uploads/source.wav", limit=len(data) - 1)
    with pytest.raises(SpeechInputError, match="hash"):
        read_verified_file(paths, "uploads/source.wav", limit=len(data), expected_hash="0" * 64)
    path.write_bytes(wav_bytes(2))
    with pytest.raises(SpeechInputError, match="changed"):
        read_verified_file(paths, "uploads/source.wav", limit=len(data), expected_identity=identity)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        "/etc/passwd",
        "C:\\private.wav",
        "uploads/../source.wav",
        "uploads/./source.wav",
    ],
)
def test_path_traversal(tmp_path: Path, relative: str) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    with pytest.raises(SpeechInputError):
        read_verified_file(paths, relative, limit=100)


@pytest.mark.parametrize(
    "kind",
    [
        "file_symlink",
        "directory_alias",
        "directory",
        pytest.param(
            "fifo",
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"), reason="FIFOs do not exist on this platform"
            ),
        ),
    ],
)
def test_rejects_symlinks_directories_and_fifo_without_blocking(tmp_path: Path, kind: str) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    (paths.uploads / "real.wav").write_bytes(wav_bytes())
    if kind == "file_symlink":
        symlink_or_skip(paths.uploads / "link.wav", paths.uploads / "real.wav")
        relative = "uploads/link.wav"
    elif kind == "directory_alias":
        symlink_or_skip(paths.root / "alias", paths.uploads, target_is_directory=True)
        relative = "alias/real.wav"
    elif kind == "directory":
        (paths.uploads / "directory.wav").mkdir()
        relative = "uploads/directory.wav"
    else:
        os.mkfifo(paths.uploads / "fifo.wav")
        relative = "uploads/fifo.wav"
    with pytest.raises(SpeechInputError):
        read_verified_file(paths, relative, limit=1024)


def test_private_staging_and_cleanup_retains_replacements_and_borrowed_files(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    directory, identity = create_staging_directory(paths)
    first = stage_file(paths, directory, wav_bytes(), ".wav")
    second = stage_file(paths, directory, b"{}", ".json")
    replacement = paths.uploads / "replacement"
    replacement.write_bytes(b"replacement")
    replacement.replace(paths.managed_path(first.relative_path))
    cleanup_files(paths, (first, second), directory, identity)
    assert paths.managed_path(first.relative_path).read_bytes() == b"replacement"
    assert not paths.managed_path(second.relative_path).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes do not exist on NTFS")
def test_private_staging_uses_owner_only_modes(tmp_path: Path) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    directory, _identity = create_staging_directory(paths)
    first = stage_file(paths, directory, wav_bytes(), ".wav")
    assert stat.S_IMODE(paths.managed_path(directory).stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.managed_path(first.relative_path).stat().st_mode) == 0o600


def test_cleanup_preserves_replaced_directory(tmp_path: Path) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    directory, identity = create_staging_directory(paths)
    item = stage_file(paths, directory, b"ours", ".json")
    original = paths.managed_path(directory)
    original.rename(original.with_name(original.name + "-moved"))
    original.mkdir()
    (original / Path(item.relative_path).name).write_bytes(b"not ours")
    cleanup_files(paths, (item,), directory, identity)
    assert (original / Path(item.relative_path).name).read_bytes() == b"not ours"


@pytest.mark.parametrize("data", [b"RIFFinvalid", b"not wav", wav_bytes()[:-10]])
def test_invalid_or_truncated_wav(data: bytes) -> None:
    with pytest.raises(SpeechInputError):
        validate_wav(data)


def test_path_replacement_during_open_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = DataPaths.from_value(tmp_path).ensure()
    source = paths.uploads / "source.wav"
    source.write_bytes(wav_bytes())
    replacement = paths.uploads / "replacement.wav"
    replacement.write_bytes(wav_bytes(2))
    original = os.open
    changed = []

    def changing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(os.fsdecode(path)).name == "source.wav" and not changed:
            changed.append(True)
            replacement.replace(source)
        return original(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", changing_open)
    # Preserve the capability test after replacing the function object.
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {changing_open})
    with pytest.raises(SpeechInputError, match="changed"):
        read_verified_file(paths, "uploads/source.wav", limit=1024)
