"""Seed a synthetic data root with a large and a small audiobook project.

    <uv env> python _bmad-output/scripts/large_project_seed.py --root <empty folder>
    <uv env> python _bmad-output/scripts/large_project_seed.py --measure <data root>

The seed runs the checkout's own services: startup migrates a fresh database,
sessions and plan revisions come from the generation services, and every take
artifact carries the audio identity the app computes for its segment, so the
takes are reusable exactly as generated audio would be. Take audio files are
not written; like a copied database without its session folders, only the
rows exist.

Defaults follow the shape counts recorded from the real copy (see --measure).
``--shape`` overrides any of them with a JSON object.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import secrets
import sqlite3
import sys
import time
import uuid
import wave
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class ProjectShape:
    """One project's plan history, mirroring a real book's revisions."""

    segments: int
    # Segment counts of earlier revisions, oldest first, as fractions of the
    # active size. The active revision is a restore of the last of them.
    earlier_revisions: list[float] = field(default_factory=lambda: [0.947, 1.0])
    # Takes per segment on each earlier revision (active + inactive).
    earlier_takes: list[float] = field(default_factory=lambda: [1.0, 2.16])
    # Extra regenerated takes on the active revision's first segments.
    regenerated_segments: int = 15
    optimized_segments: int = 15
    chapter_every: int = 46
    # A rejected draft between the last earlier revision and the restore:
    # its size as a fraction of the active size (0 for none), with some
    # segments removed and no audio, like an abandoned resegmentation.
    draft_revision: float = 0.0
    draft_removed: int = 0


@dataclass
class Shape:
    large: ProjectShape = field(default_factory=lambda: ProjectShape(5167))
    small: ProjectShape = field(default_factory=lambda: ProjectShape(
        189, earlier_revisions=[1.01, 1.0], earlier_takes=[6.0, 2.0],
        regenerated_segments=1, optimized_segments=1, chapter_every=60,
        draft_revision=1.058, draft_removed=11,
    ))
    text_mean_chars: int = 51
    text_max_chars: int = 475
    voices: int = 37
    provenance: bool = False
    # Casting as in the real books: audio.cpp voices, a narrator, two
    # categories and named characters, each cast slot a retained reference.
    casting: bool = True
    characters: int = 24
    small_characters: int = 4
    generation_prompt: bool = False
    # An adopted speaker-analysis plan on the last earlier and the active
    # revision, as speech XML: the share of segments with one or two
    # character dialogue parts.
    speaker_plans: bool = True
    one_speaker_fraction: float = 0.301
    two_speaker_fraction: float = 0.043
    # A third, uncast project that only fills the shared tables.
    filler_segments: int = 11424
    filler_takes: float = 0.27
    seed: int = 20261110

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any]) -> Shape:
        shape = cls()
        for key, value in overrides.items():
            if key in {"large", "small"}:
                project = getattr(shape, key)
                for name, item in dict(value).items():
                    if name not in {f.name for f in fields(ProjectShape)}:
                        raise ValueError(f"Unknown project shape field: {name}")
                    setattr(project, name, item)
            elif key in {f.name for f in fields(cls)}:
                setattr(shape, key, value)
            else:
                raise ValueError(f"Unknown shape field: {key}")
        return shape


# ---------------------------------------------------------------- measure


def measure(root: Path) -> dict[str, Any]:
    """Shape counts of a data root's plans: counts only, never text or names."""
    database = root / "pandrator.sqlite3"
    with contextlib.closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        def one(sql: str, *args: Any) -> Any:
            return db.execute(sql, args).fetchone()

        plans = []
        for session_id, active_id in db.execute(
            "SELECT session_id, active_revision_id FROM generation_plans"
        ).fetchall():
            revisions = []
            for revision_id, number in db.execute(
                """SELECT r.id, r.revision_number FROM generation_plan_revisions r
                   JOIN generation_plans p ON p.id = r.plan_id
                   WHERE p.session_id = ? ORDER BY r.revision_number""",
                (session_id,),
            ).fetchall():
                segments, removed, mean_text, max_text, optimized, completed = one(
                    """SELECT count(*), coalesce(sum(removed), 0), coalesce(avg(length(text)), 0),
                              coalesce(max(length(text)), 0),
                              coalesce(sum(optimized_text IS NOT NULL), 0),
                              coalesce(sum(status = 'completed'), 0)
                       FROM generation_segments WHERE plan_revision_id = ?""",
                    revision_id,
                )
                takes, active, with_take = one(
                    """SELECT count(t.id), coalesce(sum(t.is_active), 0),
                              count(DISTINCT t.generation_segment_id)
                       FROM audio_takes t JOIN generation_segments s ON s.id = t.generation_segment_id
                       WHERE s.plan_revision_id = ?""",
                    revision_id,
                )
                revisions.append({
                    "number": number, "active": revision_id == active_id,
                    "segments": segments, "removed": removed, "completed": completed,
                    "text_mean_chars": round(mean_text, 1), "text_max_chars": max_text,
                    "optimized": optimized, "takes": takes, "active_takes": active,
                    "segments_with_takes": with_take,
                    "speakers": one("SELECT count(DISTINCT speaker) FROM generation_segments "
                                    "WHERE plan_revision_id = ?", revision_id)[0],
                    "voice_assigned": one("SELECT count(*) FROM generation_segments WHERE "
                                          "plan_revision_id = ? AND (voice_id IS NOT NULL OR voice IS NOT NULL)",
                                          revision_id)[0],
                    "with_provenance": one("SELECT count(*) FROM generation_segments WHERE "
                                           "plan_revision_id = ? AND speech_block_provenance_json != '{}'",
                                           revision_id)[0],
                    "performance_plans": one("SELECT count(*) FROM performance_plans WHERE "
                                             "plan_revision_id = ?", revision_id)[0],
                })
            plans.append({
                "revisions": revisions,
                "speech_references": one("SELECT count(*) FROM project_speech_references "
                                         "WHERE session_id = ?", session_id)[0],
                "performance_plans": one("SELECT count(*) FROM performance_plans "
                                         "WHERE session_id = ?", session_id)[0],
                "generation_runs": one("SELECT count(*) FROM generation_runs "
                                       "WHERE session_id = ?", session_id)[0],
                "casting_enabled": bool(one(
                    "SELECT count(*) FROM session_settings WHERE session_id = ? AND section = 'tts' "
                    "AND json_extract(value_json, '$.casting_enabled') = 1", session_id)[0]),
            })
        plans.sort(key=lambda plan: -max(r["segments"] for r in plan["revisions"]))
        totals = {
            table: one(f"SELECT count(*) FROM {table}")[0]  # noqa: S608 - fixed table names
            for table in ("sessions", "generation_segments", "audio_takes", "artifacts",
                          "voices", "voice_samples", "project_speech_references",
                          "performance_plans", "jobs", "job_events", "audit_events")
        }
        take_spread = dict(db.execute(
            "SELECT k, count(*) FROM (SELECT count(*) AS k FROM audio_takes "
            "GROUP BY generation_segment_id) GROUP BY k ORDER BY k"
        ).fetchall())
        take_metadata = one("SELECT coalesce(avg(length(metadata_json)), 0) FROM artifacts "
                            "WHERE role = 'generation_take'")[0]
    return {"plans": plans, "totals": totals, "takes_per_segment": take_spread,
            "take_artifact_metadata_chars": round(take_metadata)}


# ---------------------------------------------------------------- seed


CAST_MODEL = "breeze_tts_2_q8_0"


def voice_slug(index: int) -> str:
    return f"seed-voice-{index:02d}"


WORDS = (
    "the a quiet evening light over stone hall window door voice answer letter castle "
    "corridor morning question silence moment slowly carefully said asked whispered never "
    "always perhaps beneath across toward between garden library staircase candle shadow "
    "forest river bridge market tower lesson teacher student friend family house night"
).split()


def synthetic_text(rng: random.Random, mean: int, maximum: int) -> str:
    target = min(maximum, max(8, int(rng.expovariate(1 / mean))))
    words: list[str] = []
    while sum(len(word) + 1 for word in words) < target:
        words.append(rng.choice(WORDS))
    sentence = " ".join(words)[:maximum].strip()
    return sentence[:1].upper() + sentence[1:] + "."


def plan_records(rng: random.Random, count: int, shape: Shape, project: ProjectShape) -> list[dict[str, Any]]:
    records = []
    for index in range(count):
        chapter = project.chapter_every and index % project.chapter_every == 0
        record: dict[str, Any] = {
            "text": f"Chapter {index // max(project.chapter_every, 1) + 1}." if chapter
            else synthetic_text(rng, shape.text_mean_chars, shape.text_max_chars),
            "node_kind": "chapter_marker" if chapter else "paragraph",
            "paragraph_break_after": True,
        }
        if shape.provenance:
            record["speech_block_provenance"] = {
                "schema_version": 1, "source_reference_namespace": "subtitle_ordinal",
                "source_cues": [{"reference": index, "start_ms": index * 1000,
                                 "end_ms": index * 1000 + 900, "display_spans": [[0, 10]],
                                 "speech_spans": [[0, 10]]}],
            }
        records.append(record)
    return records


class Seeder:
    def __init__(self, root: Path, shape: Shape, log):
        from unittest.mock import patch

        from pandrator.web.api import create_app
        from pandrator.web.auth import BootstrapTokenStore

        self.log = log
        self.shape = shape
        self.rng = random.Random(shape.seed)
        self._xml_choices: dict[tuple[str, int, str], str] = {}
        self._gpu = patch("pandrator.web.capabilities.probe_gpu", return_value={
            "available": False, "devices": [], "guidance": "Synthetic seed."})
        self._gpu.start()
        bootstrap = BootstrapTokenStore()
        token = bootstrap.issue()
        self.app = create_app(data_root=root, testing=True, bootstrap_tokens=bootstrap)
        self.client = self.app.test_client()
        csrf = self.client.post("/api/v1/auth/bootstrap", json={"token": token}).get_json()["csrf_token"]
        self.headers = {"X-CSRF-Token": csrf}
        self.services = self.app.extensions["pandrator"]
        self.database = self.services["database"]
        self.generation = self.services["generation"]

    def close(self, *, completed: bool = True) -> None:
        from pandrator.web.auth import AuthService

        try:
            if completed:
                # A random owner password nobody needs: tokens are minted in-process.
                AuthService(self.database).initialize_owner(secrets.token_urlsafe(24))
        finally:
            try:
                self.database.dispose()
            finally:
                self._gpu.stop()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.open(path, method=method, json=body, headers={
            **self.headers, "Idempotency-Key": str(uuid.uuid4())})
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} returned {response.status_code}: {response.get_data(as_text=True)[:300]}")
        return response.get_json()

    def wave_file(self, folder: str, name: str) -> Path:
        directory = self.services["paths"].uploads / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(bytes(self.rng.getrandbits(8) for _ in range(3200)))
        return path

    def seed_voices(self) -> None:
        """Library voices with one reviewed sample each, linked for audio.cpp."""
        from pandrator.web.models import Voice, VoiceSample

        for index in range(self.shape.voices):
            voice = self.request("POST", "/api/v1/voices", {"name": f"Seed voice {index:02d}"})
            path = self.services["paths"].voices / voice["id"] / "sample.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.wave_file("seed-voices", f"voice-{index}").read_bytes())
            artifact = self.services["artifacts"].register(
                path, kind="audio", role="voice_sample", workspace_id=voice["workspace_id"],
            )
            with self.database.immediate_session() as session:
                session.add(VoiceSample(
                    workspace_id=voice["workspace_id"], voice_id=voice["id"], artifact_id=artifact.id,
                    transcript=f"Seed voice {index} reads a short reviewed line.",
                    transcript_language="en", transcript_reviewed=True,
                ))
                session.get_one(Voice, voice["id"]).metadata_json = {"providers": {"audio_cpp": {
                    "status": "ready", "resource_kind": "linked_reference",
                    "voice_id": voice_slug(index),
                }}}

    def create_session(self, name: str) -> str:
        return self.request("POST", "/api/v1/sessions", {"name": name, "workflow_kind": "audiobook"})["id"]

    def voice_ids(self) -> list[str]:
        from sqlalchemy import select

        from pandrator.web.models import Voice

        with self.database.session() as session:
            return list(session.scalars(select(Voice.id).order_by(Voice.name)))

    def enable_casting(self, session_id: str, characters: int) -> None:
        """audio.cpp narration with a cast, saved through the session APIs."""
        voices = self.voice_ids()
        if len(voices) < 3:
            raise ValueError("Casting needs at least three seeded voices.")
        current = self.request("GET", f"/api/v1/sessions/{session_id}/settings/tts")
        value = {
            **current["effective"], "service": "audio_cpp", "model": CAST_MODEL,
            "xtts_model": CAST_MODEL, "voice": "", "speaker": "", "language": "en",
            "casting_enabled": True, "voice_mode_version": 1,
            "generation_prompt": "Read warmly, with distinct character voices."
            if self.shape.generation_prompt else "",
        }
        revision = current["revision"]
        response = self.client.put(
            f"/api/v1/sessions/{session_id}/settings/tts", json={"value": value},
            headers={**self.headers, "If-Match": f'"{revision}"',
                     "Idempotency-Key": str(uuid.uuid4())},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Casting settings returned {response.status_code}: "
                               f"{response.get_data(as_text=True)[:300]}")

        def bind(index: int) -> dict[str, Any]:
            return {"voice": "", "voice_id": voices[index % len(voices)], "service": "audio_cpp",
                    "model": CAST_MODEL, "voice_description": ""}

        roster = [{"id": f"character_{index:02d}", "display_name": f"Character {index:02d}",
                   "aliases": [f"C{index:02d}"], "voice_category": "male" if index % 2 else "female"}
                  for index in range(characters)]
        cast = {
            "narrator": bind(0),
            "categories": {"female": bind(1), "male": bind(2)},
            "characters": {item["id"]: bind(3 + index) for index, item in enumerate(roster)},
        }
        response = self.client.put(
            f"/api/v1/sessions/{session_id}/generation-controls",
            json={"expected_revision": 0, "characters": roster, "cast": cast},
            headers={**self.headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Cast write returned {response.status_code}: "
                               f"{response.get_data(as_text=True)[:300]}")

    def identities(
        self, session_id: str, revision_id: str, *, segment_ids: set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        from pandrator.web.generation_audio_identity import plan_audio_identities

        snapshot, _ = self.services["workspace_settings"].resolve(session_id, run_override={})
        with self.database.session() as session:
            return plan_audio_identities(session, revision_id, snapshot, segment_ids=segment_ids)

    def seed_takes(
        self, session_id: str, revision_id: str, per_segment: float, *,
        limit: int | None = None,
    ) -> None:
        """Completed takes with the app's identity; extra takes are older and inactive."""
        from sqlalchemy import select, update

        from pandrator.web.generation_audio_identity import IDENTITY_KEY
        from pandrator.web.models import Artifact, AudioTake, GenerationSegment, SessionRecord
        from pandrator.web.projects.models import Project

        expected = self.identities(session_id, revision_id)
        with self.database.session() as session:
            record = session.get(SessionRecord, session_id)
            project = session.get(Project, record.project_id)
            workspace_id, project_id = project.workspace_id, project.id
            segments = list(session.execute(
                select(GenerationSegment.id, GenerationSegment.text, GenerationSegment.ordinal)
                .where(GenerationSegment.plan_revision_id == revision_id)
                .order_by(GenerationSegment.ordinal)
            ))
        if limit is not None:
            segments = segments[:limit]
        extra_fraction = max(0.0, per_segment - 1.0)
        artifacts, takes = [], []
        for segment_id, text, _ordinal in segments:
            older = int(extra_fraction) + (1 if self.rng.random() < extra_fraction % 1 else 0)
            count = 1 + older
            for position in range(count):
                active = position == count - 1
                identity = dict(expected[segment_id])
                if not active:
                    identity["settings_hash"] = uuid.uuid4().hex * 2
                artifact_id = str(uuid.uuid4())
                artifacts.append(Artifact(
                    id=artifact_id, workspace_id=workspace_id, project_id=project_id,
                    session_id=session_id, kind="audio", role="generation_take",
                    relative_path=f"sessions/{session_id}/generation/{artifact_id}.wav",
                    mime_type="audio/wav", size_bytes=96044,
                    content_hash=secrets.token_hex(32), state="current",
                    metadata_json={
                        IDENTITY_KEY: identity, "generation_segment_id": segment_id,
                        "generation_run_id": None, "generation_task_run_id": None, "kind": "tts",
                        "source_text": text, "synthesized_text": text, "speaker": None,
                        "llm_optimized": False, "llm_model": None,
                        "render_parts": [{"start": 0, "end": len(text), "voice": "narrator",
                                          "duration_ms": 3000, "text": text}],
                    },
                ))
                takes.append(AudioTake(
                    generation_segment_id=segment_id, artifact_id=artifact_id, status="completed",
                    is_active=active, duration_ms=3000, kind="tts",
                ))
        with self.database.immediate_session() as session:
            session.add_all(artifacts)
            session.flush()
            session.add_all(takes)
            for offset in range(0, len(segments), 500):
                session.execute(update(GenerationSegment).where(GenerationSegment.id.in_(
                    [row[0] for row in segments[offset:offset + 500]]
                )).values(status="completed"))

    def seed_project(self, name: str, project: ProjectShape, characters: int) -> str:
        session_id = self.create_session(name)
        if self.shape.casting:
            self.enable_casting(session_id, characters)
        sizes = [max(1, round(project.segments * fraction)) for fraction in project.earlier_revisions]
        revision_id = None
        for number, (size, takes) in enumerate(zip(sizes, project.earlier_takes, strict=True), 1):
            started = time.perf_counter()
            records = plan_records(self.rng, size, self.shape, project)
            plan = self.generation.create_plan(session_id, source_revision_id=None, settings={},
                                               segments=records)
            revision_id = plan["active_revision_id"]
            if self.shape.casting and self.shape.speaker_plans and number == len(sizes):
                self.adopt_speaker_plan(session_id, revision_id, characters)
            self.seed_takes(session_id, revision_id, takes)
            self.log(f"  {name}: revision {number}, {size} segments in {time.perf_counter() - started:.0f}s")
        assert revision_id is not None
        if sizes[-1] != project.segments:
            raise ValueError("The last earlier revision must have the active size (fraction 1.0).")
        # The active revision restores the last one, cloning its takes as the
        # app does after an edit is undone.
        from pandrator.web.generation_topology import GenerationTopologyService

        expected_id = revision_id
        if project.draft_revision:
            expected_id = self.seed_draft(session_id, project)
        result = GenerationTopologyService(self.database, self.services["jobs"]).revise_topology(
            session_id, expected_id, {"action": "restore", "target_revision_id": revision_id},
        )
        active_id = result["plan_revision_id"]
        # Optimized text changes a segment's audio, so those segments are the
        # ones regenerated, as in the real book.
        optimized = self.mark_optimized(active_id, project.optimized_segments)
        if self.shape.casting and self.shape.speaker_plans:
            self.adopt_speaker_plan(session_id, active_id, characters)
        if project.regenerated_segments:
            self.add_regenerated_takes(session_id, active_id, project.regenerated_segments, optimized)
        return session_id

    def speech_xml(self, segment_id: str, text: str, characters: int, key: tuple[str, int]) -> str:
        """Narration with zero, one or two character dialogue parts.

        The same position and text keep the same parts in every revision, as
        a restored or copied plan keeps its adopted speakers.
        """
        from xml.sax.saxutils import escape

        remembered = self._xml_choices.get((*key, text))
        if remembered is not None:
            return remembered.replace("@SEGMENT@", segment_id)
        draw = self.rng.random()
        speakers = 0
        if characters and draw < self.shape.two_speaker_fraction:
            speakers = 2
        elif characters and draw < self.shape.two_speaker_fraction + self.shape.one_speaker_fraction:
            speakers = 1
        words = text.split(" ")
        if len(words) < speakers + 2:
            speakers = 0
        body = escape(text)
        if speakers:
            cuts = sorted(self.rng.sample(range(1, len(words)), speakers))
            pieces = [" ".join(words[start:end]) for start, end in
                      zip([0, *cuts], [*cuts, len(words)], strict=True)]
            refs = [f"character_{self.rng.randrange(characters):02d}" for _ in range(speakers)]
            body = escape(pieces[0]) + " " + " ".join(
                f'<dialogue><speaker ref="{ref}">{escape(piece)}</speaker></dialogue>'
                for ref, piece in zip(refs, pieces[1:], strict=True)
            )
        self._xml_choices[(*key, text)] = f'<segment id="@SEGMENT@">{body}</segment>'
        return f'<segment id="{segment_id}">{body}</segment>'

    def adopt_speaker_plan(self, session_id: str, revision_id: str, characters: int) -> None:
        """Create, annotate and adopt a manual speaker plan through the API."""
        from sqlalchemy import select

        from pandrator.web.models import GenerationSegment

        started = time.perf_counter()
        base = f"/api/v1/sessions/{session_id}/performance-plans"
        plan = self.request("POST", base + "?include_units=false", {
            "expected_plan_revision_id": revision_id, "purpose": "speakers", "mode": "manual",
            "annotation_format": "xml", "batch_size": 32,
        })
        with self.database.session() as session:
            rows = list(session.execute(
                select(GenerationSegment.id, GenerationSegment.text, GenerationSegment.optimized_text)
                .where(GenerationSegment.plan_revision_id == revision_id,
                       GenerationSegment.removed.is_(False))
                .order_by(GenerationSegment.ordinal)
            ))
        items = [{"segment_id": segment_id,
                  "speech_xml": self.speech_xml(segment_id, optimized or text, characters,
                                                (session_id, index))}
                 for index, (segment_id, text, optimized) in enumerate(rows)]
        version = plan["version"]
        for offset in range(0, len(items), 100):
            version = self.request("PATCH", f"{base}/{plan['id']}", {
                "expected_version": version, "items": items[offset:offset + 100],
            })["version"]
        self.request("POST", f"{base}/{plan['id']}/adopt", {
            "expected_version": version, "accept_unanalysed": True, "enable": False,
        })
        self.log(f"  speaker plan on {len(items)} segments in {time.perf_counter() - started:.0f}s")

    def seed_draft(self, session_id: str, project: ProjectShape) -> str:
        """A draft revision without audio, some of its segments removed."""
        from sqlalchemy import select, update

        from pandrator.web.models import GenerationSegment

        size = max(1, round(project.segments * project.draft_revision))
        records = plan_records(self.rng, size, self.shape, project)
        draft_id = self.generation.create_plan(session_id, source_revision_id=None, settings={},
                                               segments=records)["active_revision_id"]
        with self.database.immediate_session() as session:
            removed = list(session.scalars(select(GenerationSegment.id).where(
                GenerationSegment.plan_revision_id == draft_id,
            ).order_by(GenerationSegment.ordinal.desc()).limit(project.draft_removed)))
            session.execute(update(GenerationSegment).where(GenerationSegment.id.in_(removed))
                            .values(removed=True))
        return draft_id

    def seed_filler(self, segments: int, takes: float) -> None:
        """An uncast single-revision book whose first part has audio."""
        started = time.perf_counter()
        session_id = self.create_session("Synthetic filler book")
        records = plan_records(self.rng, segments, self.shape, ProjectShape(segments))
        plan = self.generation.create_plan(session_id, source_revision_id=None, settings={},
                                           segments=records)
        self.seed_takes(session_id, plan["active_revision_id"], 1.0, limit=round(segments * takes))
        self.log(f"  filler: {segments} segments in {time.perf_counter() - started:.0f}s")

    def mark_optimized(self, revision_id: str, count: int) -> list[str]:
        from sqlalchemy import select

        from pandrator.web.models import GenerationSegment

        marked = []
        with self.database.immediate_session() as session:
            for segment in session.scalars(select(GenerationSegment).where(
                GenerationSegment.plan_revision_id == revision_id,
                GenerationSegment.node_kind == "paragraph",
            ).order_by(GenerationSegment.ordinal).limit(count)):
                segment.optimized_text = segment.text.rstrip(".") + ", reviewed."
                segment.optimization_status = "reviewed"
                segment.optimization_reviewed = True
                marked.append(segment.id)
        return marked

    def add_regenerated_takes(
        self, session_id: str, revision_id: str, count: int, preferred: list[str],
    ) -> None:
        """A newer active take on a few segments, superseding the cloned one."""
        from sqlalchemy import select, update

        from pandrator.web.models import AudioTake, GenerationSegment

        first = list(preferred[:count])
        with self.database.session() as session:
            for segment_id in session.scalars(select(GenerationSegment.id).where(
                    GenerationSegment.plan_revision_id == revision_id).order_by(GenerationSegment.ordinal)):
                if len(first) >= count:
                    break
                if segment_id not in first:
                    first.append(segment_id)
        with self.database.immediate_session() as session:
            session.execute(update(AudioTake).where(AudioTake.generation_segment_id.in_(first))
                            .values(is_active=False))
        self.seed_takes_for(session_id, revision_id, set(first))

    def seed_takes_for(self, session_id: str, revision_id: str, segment_ids: set[str]) -> None:
        from pandrator.web.generation_audio_identity import IDENTITY_KEY
        from pandrator.web.models import Artifact, AudioTake, SessionRecord
        from pandrator.web.projects.models import Project

        expected = self.identities(session_id, revision_id, segment_ids=segment_ids)
        with self.database.immediate_session() as session:
            record = session.get(SessionRecord, session_id)
            project = session.get(Project, record.project_id)
            for segment_id in sorted(segment_ids):
                artifact_id = str(uuid.uuid4())
                session.add(Artifact(
                    id=artifact_id, workspace_id=project.workspace_id, project_id=project.id,
                    session_id=session_id, kind="audio", role="generation_take",
                    relative_path=f"sessions/{session_id}/generation/{artifact_id}.wav",
                    mime_type="audio/wav", size_bytes=96044, content_hash=secrets.token_hex(32),
                    metadata_json={IDENTITY_KEY: expected[segment_id], "generation_segment_id": segment_id,
                                   "kind": "tts"},
                ))
                session.flush()
                session.add(AudioTake(generation_segment_id=segment_id, artifact_id=artifact_id,
                                      status="completed", is_active=True, duration_ms=3000))


def seed(root: Path, shape: Shape, log) -> dict[str, Any]:
    seeder = Seeder(root, shape, log)
    try:
        started = time.perf_counter()
        seeder.seed_voices()
        large = seeder.seed_project("Synthetic large book", shape.large, shape.characters)
        small = seeder.seed_project("Synthetic small book", shape.small, shape.small_characters)
        if shape.filler_segments:
            seeder.seed_filler(shape.filler_segments, shape.filler_takes)
        log(f"Seeded in {time.perf_counter() - started:.0f}s")
    except BaseException:
        # Clean up without initializing the owner, so the original error shows.
        seeder.close(completed=False)
        raise
    seeder.close()
    return {"large_session": large, "small_session": small, "shape": asdict(shape)}


def inside_install(root: Path) -> bool:
    """True for a Manager install's data folder: a sibling app/ with current*.json pointers."""
    app = root.resolve().parent / "app"
    return app.is_dir() and any(app.glob("current*.json"))


def root_is_empty(root: Path) -> bool:
    return not root.exists() or not any(root.iterdir())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--root", type=Path, help="Empty folder to seed.")
    target.add_argument("--measure", type=Path, help="Print the shape counts of a data root.")
    parser.add_argument("--segments", type=int, help="Active size of the large project.")
    parser.add_argument("--small-segments", type=int, help="Active size of the small project.")
    parser.add_argument("--shape", help="JSON object overriding shape fields.")
    parser.add_argument("--force", action="store_true", help="Seed into a non-empty root.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.measure:
        print(json.dumps(measure(args.measure.expanduser().resolve()), indent=2))
        return 0
    root = args.root.expanduser().resolve()
    if inside_install(root):
        # --force never overrides this: startup would migrate the install's data.
        print("Refused: the root belongs to a Pandrator install.", file=sys.stderr)
        return 2
    if not root_is_empty(root) and not args.force:
        print("Refused: the root is not empty. Pass --force to seed into it anyway.", file=sys.stderr)
        return 2
    shape = Shape.from_overrides(json.loads(args.shape) if args.shape else {})
    if args.segments:
        shape.large.segments = args.segments
    if args.small_segments:
        shape.small.segments = args.small_segments
    root.mkdir(parents=True, exist_ok=True)
    result = seed(root, shape, lambda message: print(message, flush=True))
    (root.parent / f"{root.name}-seed.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"large_session": result["large_session"], "small_session": result["small_session"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
