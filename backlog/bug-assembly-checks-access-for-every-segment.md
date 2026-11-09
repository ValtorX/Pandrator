---
id: 3
type: bug
title: "Assembling a book repeats its access check for every segment"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: low
severity: P3
---

# Assembling a book repeats its access check for every segment

## Description

Database reads during assembly grow with the book: 85 SELECTs at 3 segments and 667 at 100. The test for this says the count must not scale with segments. On a long book that is thousands of extra reads, though each one is read-only and cheap.

## Reproduction

1. Run `test_assembly_select_count_does_not_scale_with_segments` in `tests/test_web_database_efficiency.py`, on `ebe7194d` or later.

Actual: about 6 SELECTs per segment in the fixture, and about 3 for real takes, since the fixture also takes a second check for takes with no duration. Expected by the test: no growth.

## Cause Hypothesis

The assembly validation loop proves access once per segment, at 3 SELECTs each. Project copy throttles the same kind of check by time. Cluster F1 of the classification has the code locations.

## Acceptance Criteria

1. **The read count matches the settled rule**
   **Given** a project at 3 and at 100 segments
   **When** each is assembled
   **Then** SELECTs per segment stay within the rule the Open question settles, and access lost mid-assembly still stops it
2. **Tests cover the condition found and fixed**
   **Given** the test suite
   **When** it runs
   **Then** `test_web_database_efficiency` passes and states that rule
3. **Or: no change is needed, with proof**
   **Given** the reproduction
   **When** it is run on the current code
   **Then** the expected behavior already holds, or the report was mistaken, with the evidence recorded in Notes — this supersedes 1 and 2

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md`, Groups
- classification — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md`, F1

## Notes

- Open question: Should the check be throttled by time, as project copy does, or should the test allow one check per segment? Throttling keeps the test's rule at about 5 product lines, and relaxing is about 3 test lines. Upstream's author owns this test, so any upstream PR leaves the choice to them.
