---
id: 1
type: bug
title: "An alternate take with another audio.cpp model is refused"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: medium
severity: P2
---

# An alternate take with another audio.cpp model is refused

## Description

With a voice selected on audio.cpp, a user opens the alternate dialog for some blocks, picks another model of the same service, and gets a 409. No take is generated. The take should generate with the chosen model and voice.

## Reproduction

1. Run `test_grouped_regeneration_checkpoint_uses_output_run_local_takes` and `test_resume_checkpoint_filtering_uses_streaming_batch_path` in `tests/test_web_generation_regeneration.py`, on `ebe7194d` or later, with an execution actor set. Both fail with this error.
2. Over HTTP, with the UI's payload shape: root run on Qwen q8 with voice Alice, then a strict-single regeneration overriding the model.

Actual: 409 `generation_unavailable`, "Retained speech binding disagrees with selected settings." Expected: 202.

| Root run | Override | Status |
|---|---|---|
| Qwen q8, voice Alice | model only, with `generation_run_id` | 409 |
| Qwen q8, voice Alice | model only, no `generation_run_id` | 409 |
| Qwen q8, voice Alice | service, model q4, voice, language | 409 |
| `audio.cpp` service spelling | model q4 | 409 |
| No voice set | model only | 202 |
| Qwen q8, voice Alice | service Gemini, model, voice | 202 |
| Non-strict (`voice_mode_version` 0) | model and voice | 202 |

Not done: clicking through the running UI.

## Cause Hypothesis

A strict-single regeneration keeps the run's retained speech binding while the segment override swaps the model. The two are merged, and the retained-input check refuses them because nothing drops the stale binding. Two product comments say the override should work. Cluster R1 of the classification has the code locations.

## Acceptance Criteria

1. **Another model generates an alternate take**
   **Given** a run on audio.cpp with a selected voice
   **When** an alternate take asks for another model of the same service
   **Then** the request is accepted and the take uses the chosen model and voice
2. **A real disagreement is still refused**
   **Given** a retained binding that truly disagrees with its settings
   **When** a run that isn't changing them uses it
   **Then** it is still refused
3. **Tests cover the condition found and fixed**
   **Given** the test suite
   **When** it runs
   **Then** the two tests in `test_web_generation_regeneration.py` pass, and each row of the table above is covered
4. **Or: no change is needed, with proof**
   **Given** the reproduction
   **When** it is run on the current code
   **Then** the expected behavior already holds, or the report was mistaken, with the evidence recorded in Notes — this supersedes 1 to 3

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md`, Real bugs
- classification — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md`, R1

## Notes

- Decision (2026-11-09): Standalone, because it is outside `epic-fork-releases` and upstream has it too. Worth offering upstream once fixed.
- Marked (2026-11-10, `cee8fa56` on `epic-fork-releases`): both reproduction tests now carry `@pytest.mark.usefixtures("host_execution_actor")`. Without it they failed earlier with `AccessDenied` and never reached this bug. They also carry `xfail(strict=True, raises=AssertionError)` naming this bug, and fail on the `202 != 409` binding error. Strict means the fix turns them into unexpected passes that fail the run; remove the markers with the fix. Upstream's #120 does not touch them.
- Done (2026-11-11, owner): upstream fixed this in its 0.14.2 release work, which reached fork `main` with the 0.14.3 merge (`bc363582`). The test that exposed it passes there, and its strict xfail marker was removed in that merge.
