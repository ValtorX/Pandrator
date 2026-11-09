---
id: 2
type: bug
title: "Saved preview settings make a generation job fail"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: medium
severity: P2
---

# Saved preview settings make a generation job fail

## Description

If `defaults.tts` holds preview routing keys, a generation job fails partway in with "Retained speech binding disagrees with selected settings." Preview routing should have no effect on generation.

## Reproduction

1. Run `tests/test_generation_workspace_execution.py::test_generation_ignores_saved_preview_routing_metadata`, on `ebe7194d` or later, alone and on either locked or range versions.

Actual: the job fails with that message. Observed binding: `adapter: audio_cpp, model: gemini-3.8-flash-tts`. Expected: the job runs on the selected service.

How often this is reached is uncertain. The UI doesn't send these keys and project settings filter them, yet `PUT defaults.tts` accepted them in the test.

## Cause Hypothesis

Freezing a run computes its adapter from settings that still hold the preview keys, while the runtime strips those keys first and computes a different adapter, so the check fires. Real bug 2 in the classification has the code locations.

## Acceptance Criteria

1. **Preview keys don't affect generation**
   **Given** `defaults.tts` holding preview routing keys
   **When** a generation job runs
   **Then** it completes on the selected service
2. **Tests cover the condition found and fixed**
   **Given** the test suite
   **When** it runs
   **Then** the named test passes, and whichever related path the fix touches, freezing or saving defaults, has its own test
3. **Or: no change is needed, with proof**
   **Given** the reproduction
   **When** it is run on the current code
   **Then** the expected behavior already holds, or the report was mistaken, with the evidence recorded in Notes — this supersedes 1 and 2

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md`, Real bugs
- classification — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md`, R2

## Notes

- Decision (2026-11-09): Standalone rather than in a sibling epic, because `epic-fork-releases` story 2 needs it first if the owner's install holds these keys. Story 2 checks that before its real run.
