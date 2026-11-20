"""The workflow speaker plan guard reads each block's own markup on real lineages.

The generation start refuses any revision that lost its lineage's adopted plan.
The workflow audio stage can run from a source that carries its own speech
markup, so it refuses only when a spoken block has none, nor a block voice or a
speaker label, and names those blocks. A direct dubbing job stores new speech
blocks on every run, so it refuses before storing them, and only when a block
without its own speech repeats text the adopted plan marks up.
"""

import dataclasses
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from pydub import AudioSegment
from sqlalchemy import func, select

from pandrator.web import models as m
from pandrator.web import performance_plans as plans
from tests.test_generation_cast_runtime import seed_cast
from tests.test_performance_plans import _cast_override, adopt, get
from tests.test_performance_plans import case as performance_case

case = performance_case

SETTINGS = {
    "service": "gemini",
    "model": "gemini-2.5-flash-tts",
    "voice": "Kore",
    "casting_enabled": True,
    "performance_enabled": False,
    "tts_batch_size": 4,
}


def _source(case, name, records, *, role="tts_optimized"):
    with case["services"]["database"].session() as session:
        storage_key = session.get(m.SessionRecord, case["session_id"]).storage_key
    path = Path(case["services"]["paths"].sessions) / storage_key / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")
    return case["services"]["artifacts"].register(
        path, kind="json", role=role, session_id=case["session_id"]
    )


def _records(xml, *, second_marked):
    """A dialogue block with cast markup, a narration block, and a blank one."""
    narration = "Membership doubled soon afterwards."
    second = {"text": narration, "original_sentence": narration}
    if second_marked:
        second["speech_xml"] = f'<segment id="2">{narration}</segment>'
    return [
        {
            "text": "He said, “Bah!” Then left.",
            "original_sentence": "He said, “Bah!” Then left.",
            "speech_xml": xml,
        },
        second,
        {"text": "   ", "original_sentence": "   "},
    ]


def _generate(case, artifact, synthesize):
    handlers = case["services"]["workflow_handlers"]
    with patch.object(handlers.tts_providers, "synthesize", side_effect=synthesize):
        return handlers.generate_audiobook_audio(
            {
                "session_id": case["session_id"],
                "source_artifact_id": artifact.id,
                "settings": dict(SETTINGS),
            },
            lambda *_args: None,
            threading.Event(),
        )


def _active_revision(case):
    with case["services"]["database"].session() as session:
        plan = session.scalar(
            select(m.GenerationPlan).where(m.GenerationPlan.session_id == case["session_id"])
        )
        revision = session.get(m.GenerationPlanRevision, plan.active_revision_id)
        return revision.id, revision.parent_revision_id


def _segment_ids(case, revision_id):
    with case["services"]["database"].session() as session:
        return list(
            session.scalars(
                select(m.GenerationSegment.id)
                .where(m.GenerationSegment.plan_revision_id == revision_id)
                .order_by(m.GenerationSegment.ordinal)
            )
        )


def _drop_speaker_labels(case, revision_id):
    """The fixture labels every block with a source speaker; plain audiobook text has none."""
    with case["services"]["database"].session() as session:
        for segment in session.scalars(
            select(m.GenerationSegment).where(m.GenerationSegment.plan_revision_id == revision_id)
        ):
            segment.speaker = None


def _count(case, model):
    with case["services"]["database"].session() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_a_source_with_markup_on_every_spoken_block_runs_from_it(case):
    adopted, xml = seed_cast(case)
    artifact = _source(case, "marked.json", _records(xml, second_marked=True))
    requests = []

    def synthesize(text, settings, **_kwargs):
        requests.append((text, settings["voice"]))
        return AudioSegment.silent(duration=20)

    assert _generate(case, artifact, synthesize)["artifact_id"]
    # The source's own cast markup voiced the dialogue.
    assert [voice for _text, voice in requests] == ["Kore", "Puck", "Kore", "Kore"]
    revision_id, parent_id = _active_revision(case)
    # The run's revision descends from the adopted one and has no plan of its
    # own, so the lineage rule applied and the per-block rule let it through.
    assert parent_id == adopted["plan_revision_id"]
    with case["services"]["database"].session() as session:
        assert plans.missing_speaker_plan(session, revision_id, SETTINGS) is not None
        assert plans.blocks_without_own_speech(session, revision_id) == []
        # The generation start keeps its broader rule on the same revision.
        with pytest.raises(ValueError, match="start again with allow_without_speaker_plan"):
            plans.refuse_missing_speaker_plan(session, revision_id, SETTINGS)


def test_a_mixed_source_is_refused_naming_its_blocks_without_markup(case):
    adopted, xml = seed_cast(case)
    artifact = _source(case, "mixed.json", _records(xml, second_marked=False))

    def synthesize(*_args, **_kwargs):
        raise AssertionError("a refused run must not synthesise")

    with pytest.raises(ValueError) as refused:
        _generate(case, artifact, synthesize)
    message = str(refused.value)
    revision_id, parent_id = _active_revision(case)
    # The job drops the blank record before storing the source.
    first, second = _segment_ids(case, revision_id)
    assert "1 spoken block has no speech markup, block voice or speaker label of its own" in message
    assert f"block 2 ({second})" in message
    # The marked dialogue block is not named.
    assert first not in message
    assert adopted["id"] in message
    assert '"Generate without the speaker plan" (allow_without_speaker_plan)' in message
    assert _count(case, m.AudioTake) == 0
    # The source's revision was stored before the stage could check it: it is
    # active, beside the adopted one, so the plan can be copied to it.
    assert parent_id == adopted["plan_revision_id"]
    revisions = _count(case, m.GenerationPlanRevision)
    # A retry from the same source reuses that revision and is refused again.
    with pytest.raises(ValueError, match="1 spoken block has no speech markup"):
        _generate(case, artifact, synthesize)
    assert _count(case, m.GenerationPlanRevision) == revisions
    assert _active_revision(case)[0] == revision_id


def test_the_reviewable_stage_refuses_a_split_of_the_adopted_revision(case):
    from pandrator.web.workflow_generation_start import run_reviewable_generation

    adopted, _xml = seed_cast(case)
    services = case["services"]
    split = services["generation"].revise_topology(
        case["session_id"],
        adopted["plan_revision_id"],
        {"action": "split", "segment_id": case["segment_ids"][1], "cursor": 3, "text_layer": "display"},
    )
    split_id = split["plan_revision_id"]
    _drop_speaker_labels(case, split_id)
    handlers = services["workflow_handlers"]
    artifact = _source(case, "prepared.json", [{"text": "Unused."}], role="prepared_text")
    generated = []
    context = dataclasses.replace(
        handlers._generation_start_context(),
        run_generation=lambda *args: generated.append(args),
    )
    job = services["jobs"].enqueue(
        "workflow.continue", {"session_id": case["session_id"]}, session_id=case["session_id"]
    )

    def run(settings):
        return run_reviewable_generation(
            context,
            {"session_id": case["session_id"], "source_artifact_id": artifact.id, "settings": {}},
            lambda value, detail=None: None,
            threading.Event(),
            resolved_snapshot={"tts": dict(settings)},
            settings_hash="caller-hash",
            job_id=job.id,
        )

    with pytest.raises(ValueError) as refused:
        run(SETTINGS)
    message = str(refused.value)
    # The dialogue block's cast lived only in the plan, so it has no markup;
    # the split's two halves start from the plan's markup and are not named.
    dialogue, left, right, last = _segment_ids(case, split_id)
    assert "2 spoken blocks have no speech markup, block voice or speaker label of their own" in message
    assert f"block 1 ({dialogue})" in message and f"block 4 ({last})" in message
    assert left not in message and right not in message
    assert adopted["id"] in message
    assert generated == []
    assert _count(case, m.GenerationRun) == 0

    # Control: with casting off the guard has nothing to protect.
    class PastTheGuard(Exception):
        pass

    with patch(
        "pandrator.web.speech_plan_workspace.freeze_speech_snapshot", side_effect=PastTheGuard()
    ):
        with pytest.raises(PastTheGuard):
            run({**SETTINGS, "casting_enabled": False})


def test_removed_and_blank_blocks_are_not_spoken(case):
    adopted, _xml = seed_cast(case)
    services = case["services"]
    split = services["generation"].revise_topology(
        case["session_id"],
        adopted["plan_revision_id"],
        {"action": "split", "segment_id": case["segment_ids"][1], "cursor": 3, "text_layer": "display"},
    )
    split_id = split["plan_revision_id"]
    _drop_speaker_labels(case, split_id)
    dialogue, _left, _right, last = _segment_ids(case, split_id)
    with services["database"].session() as session:
        assert plans.blocks_without_own_speech(session, split_id) == [(0, dialogue), (3, last)]
        removed = session.get(m.GenerationSegment, dialogue)
        removed.removed = True
        blank = session.get(m.GenerationSegment, last)
        blank.text, blank.optimized_text = "  ", None
    with services["database"].session() as session:
        # Nothing is left to voice as narration, so the per-block rule lets it run.
        assert plans.blocks_without_own_speech(session, split_id) == []
        plans.refuse_missing_speaker_plan(session, split_id, SETTINGS, from_generation_start=False)
        # The generation start keeps refusing on the lineage alone.
        with pytest.raises(ValueError, match="start again with allow_without_speaker_plan"):
            plans.refuse_missing_speaker_plan(session, split_id, SETTINGS)


def test_a_block_voice_or_speaker_label_is_its_own_speech(case):
    adopted, _xml = seed_cast(case)
    services = case["services"]
    split = services["generation"].revise_topology(
        case["session_id"],
        adopted["plan_revision_id"],
        {"action": "split", "segment_id": case["segment_ids"][1], "cursor": 3, "text_layer": "display"},
    )
    split_id = split["plan_revision_id"]
    _drop_speaker_labels(case, split_id)
    dialogue, _left, _right, last = _segment_ids(case, split_id)
    with services["database"].session() as session:
        assert plans.blocks_without_own_speech(session, split_id) == [(0, dialogue), (3, last)]
    with services["database"].session() as session:
        # A diarised speaker label is cast through source_speakers, and a block
        # voice applies to the whole block: neither falls back to the narrator.
        session.get(m.GenerationSegment, dialogue).speaker = "SPEAKER_01"
        session.get(m.GenerationSegment, last).voice = "Puck"
    with services["database"].session() as session:
        assert plans.blocks_without_own_speech(session, split_id) == []
        plans.refuse_missing_speaker_plan(session, split_id, SETTINGS, from_generation_start=False)
        # The generation start keeps refusing on the lineage alone.
        with pytest.raises(ValueError, match="start again with allow_without_speaker_plan"):
            plans.refuse_missing_speaker_plan(session, split_id, SETTINGS)


# ``seed_cast`` marks up the first block's dialogue; adoption gives the other
# blocks their plain text, which the narrator reads the same without the plan.
DIALOGUE = "He said, “Bah!” Then left."
NARRATION = "Yet this defeat would be temporary."


def _dub(case, artifact):
    """Run a direct dubbing audio job up to the freeze; the revision it froze."""
    from pandrator.web.workflow_generation_automatic import generate_audio

    handlers = case["services"]["workflow_handlers"]
    _artifact, path = handlers._resolve_input(artifact.id)
    frozen = []

    class PastTheGuards(Exception):
        pass

    def freeze(_session, revision_id, _snapshot):
        frozen.append(revision_id)
        raise PastTheGuards()

    with patch("pandrator.web.speech_plan_workspace.freeze_speech_snapshot", side_effect=freeze):
        with pytest.raises(PastTheGuards):
            generate_audio(
                handlers._automatic_generation_context(),
                case["session_id"],
                artifact,
                path,
                dict(SETTINGS),
                lambda *_args, **_kwargs: None,
                threading.Event(),
                role="dubbing_audio",
            )
    return frozen[0]


def _plan_revision_number(case, revision_id):
    with case["services"]["database"].session() as session:
        return session.get(m.GenerationPlanRevision, revision_id).revision_number


def test_direct_dubbing_runs_from_text_the_speaker_plan_does_not_mark_up(case):
    adopted, _xml = seed_cast(case)
    current = adopted["plan_revision_id"]
    assert _active_revision(case)[0] == current
    revisions = _count(case, m.GenerationPlanRevision)
    # A re-translated line, and a line whose plan entry is its plain text.
    artifact = _source(
        case,
        "retranslated.json",
        [{"text": "Il a dit « Bah ! », puis il est parti."}, {"text": NARRATION}],
        role="speech_blocks",
    )
    revision_id = _dub(case, artifact)
    assert _count(case, m.GenerationPlanRevision) == revisions + 1
    assert _active_revision(case) == (revision_id, current)
    with case["services"]["database"].session() as session:
        # The stage rule would have refused this revision, so the job's own
        # guard is the only one it passes.
        assert plans.missing_speaker_plan(session, revision_id, SETTINGS) is not None
        assert len(plans.blocks_without_own_speech(session, revision_id)) == 2


def test_direct_dubbing_refuses_a_rerun_of_the_marked_up_text(case):
    adopted, _xml = seed_cast(case)
    current = adopted["plan_revision_id"]
    revisions = _count(case, m.GenerationPlanRevision)
    artifact = _source(
        case,
        "rerun.json",
        [{"text": NARRATION}, {"text": DIALOGUE}, {"text": "Membership doubled soon afterwards."}],
        role="speech_blocks",
    )
    with pytest.raises(ValueError) as refused:
        _dub(case, artifact)
    assert str(refused.value) == (
        "Casting is on, but this audio job stores its speech blocks as a new speech plan "
        "revision with no adopted speaker plan, and 1 speech block repeats text the speaker "
        "plan annotates and has no speech markup, block voice or speaker label of its own, "
        "so it would be voiced by the narrator: block 2. "
        f"Speech plan revision {_plan_revision_number(case, current)}, the current one, has "
        f"adopted speaker plan {adopted['id']}. Generate from the generation start instead, "
        "which voices the current revision with it."
    )
    # Refused before storing a revision.
    assert _count(case, m.GenerationPlanRevision) == revisions
    assert _active_revision(case)[0] == current
    # A diarised speaker label casts the same line without the plan.
    labelled = _source(
        case, "labelled.json", [{"text": DIALOGUE, "speaker": "SPEAKER_00"}], role="speech_blocks"
    )
    assert _dub(case, labelled) != current

    refuse = plans.refuse_speaker_plan_loss_for_new_revision
    with case["services"]["database"].session() as session:
        # A block voice, a voice id or the block's own markup does too.
        refuse(
            session,
            current,
            SETTINGS,
            [
                {"text": DIALOGUE, "voice": "Puck"},
                {"text": DIALOGUE, "voice_id": "voice-c"},
                {"text": DIALOGUE, "speech_plan": {"speech_xml": f'<segment id="1">{DIALOGUE}</segment>'}},
            ],
        )
        # A record whose speech-optimised text is the marked-up line matches.
        with pytest.raises(ValueError, match=r": block 1\. "):
            refuse(
                session,
                current,
                SETTINGS,
                [{"text": "He said Bah, then left.", "tts_optimized_sentence": DIALOGUE}],
            )
        # Five blocks are named, then counted.
        with pytest.raises(ValueError) as many:
            refuse(session, current, SETTINGS, [{"text": DIALOGUE}] * 7)
        # Casting off, or performance on (which refuses on its own), is left alone.
        for other in ({}, {**SETTINGS, "performance_enabled": True}):
            refuse(session, current, other, [{"text": DIALOGUE}])
    assert (
        "7 speech blocks repeat text the speaker plan annotates and have no speech markup, "
        "block voice or speaker label of their own, so they would be voiced by the narrator: "
        "block 1, block 2, block 3, block 4, block 5 and 2 more. "
    ) in str(many.value)


def test_direct_dubbing_after_a_split_ends_at_the_generation_start(case):
    adopted, _xml = seed_cast(case)
    services = case["services"]
    split = services["generation"].revise_topology(
        case["session_id"],
        adopted["plan_revision_id"],
        {"action": "split", "segment_id": case["segment_ids"][1], "cursor": 3, "text_layer": "display"},
    )
    split_id = split["plan_revision_id"]
    assert _active_revision(case)[0] == split_id
    revisions = _count(case, m.GenerationPlanRevision)
    artifact = _source(case, "split-rerun.json", [{"text": DIALOGUE}], role="speech_blocks")
    with pytest.raises(ValueError) as refused:
        _dub(case, artifact)
    message = str(refused.value)
    assert ": block 1. " in message
    number = _plan_revision_number(case, adopted["plan_revision_id"])
    assert f"Speech plan revision {number} has adopted speaker plan {adopted['id']}." in message
    assert "the current one" not in message
    assert message.endswith(
        "Copy it to the current revision and adopt it, then generate from the generation "
        'start, or generate there with "Generate without the speaker plan" '
        "(allow_without_speaker_plan). This job cannot use a copied plan, so running it "
        "again on the same text is refused again."
    )
    assert _count(case, m.GenerationPlanRevision) == revisions

    # Following the advice: copy the plan to the split, review the dialogue
    # block again (the split changed its context, so the copy drops its
    # unlocked entry), and adopt it; the generation start then has the plan.
    response = case["post"](
        "",
        {
            "expected_plan_revision_id": split_id,
            "copy_from_id": adopted["id"],
            "annotation_format": "xml",
        },
    )
    assert response.status_code == 201, response.get_json()
    copy = response.get_json()
    dialogue_id = _segment_ids(case, split_id)[0]
    reviewed = case["post"](
        "/" + copy["id"],
        {
            "expected_version": copy["version"],
            "items": [
                {
                    "segment_id": dialogue_id,
                    "speech_xml": _xml.replace(case["segment_ids"][0], dialogue_id),
                }
            ],
        },
        method="patch",
    )
    assert reviewed.status_code == 200, reviewed.get_json()
    copied = adopt(case, get(case, copy))
    preview = services["generation"].preview_selection(
        case["session_id"], run_override=_cast_override()
    )
    assert preview["missing_speaker_plan"] is None
    # The job itself is refused again, as it said, and now names the current
    # revision's plan and the generation start, with nothing to copy.
    with pytest.raises(ValueError) as again:
        _dub(case, artifact)
    assert (
        f"Speech plan revision {_plan_revision_number(case, split_id)}, the current one, has "
        f"adopted speaker plan {copied['id']}. Generate from the generation start instead"
    ) in str(again.value)
    assert _count(case, m.GenerationPlanRevision) == revisions
