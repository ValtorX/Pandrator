---
id: 5
type: bug
title: "Long data folder paths break export and generation on Windows"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: low
severity: P3
---

# Long data folder paths break export and generation on Windows

## Description

On Windows without long paths enabled, a data folder path that is moderately long breaks soundtrack export and then generation, with a misleading `FileNotFoundError` or `WinError 3`. Typical install paths fit. The user should be told what's wrong rather than getting a file-not-found error.

## Reproduction

1. On Windows with `LongPathsEnabled=0`, set a data root longer than the limits below.
2. Run a soundtrack export, then a generation.

Actual: soundtrack export failed with a 74-character root and passed with 69. Takes failed with 102 and passed with 74. `mkdir` succeeds first, so the failure comes at the file open. Expected: a clear message, or no failure.

## Cause Hypothesis

The fixed path below the data root is about 175 characters for a take and 185 or more for a soundtrack temp file, which uses up most of the 260-character limit. Nothing checks the length up front.

## Acceptance Criteria

1. **A too-long data root is explained**
   **Given** a data root too long for the paths the app writes below it
   **When** the app starts, or the user reaches the step that would fail
   **Then** the message names the data root and the limit
2. **Tests cover the condition found and fixed**
   **Given** the test suite
   **When** it runs
   **Then** a test covers a root over the limit and one under it
3. **Or: no change is needed, with proof**
   **Given** the reproduction
   **When** it is run on the current code
   **Then** the expected behavior already holds, or the report was mistaken, with the evidence recorded in Notes — this supersedes 1 and 2

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md`, Product gaps found along the way
- classification — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md`, Latent product note

## Notes

- Decision (2026-11-09): Enabling long paths system-wide stays out of scope, as in `epic-fork-releases`.
