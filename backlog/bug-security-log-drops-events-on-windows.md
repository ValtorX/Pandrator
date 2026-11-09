---
id: 4
type: bug
title: "The security log file drops every event on Windows"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: medium
severity: P2
---

# The security log file drops every event on Windows

## Description

On Windows, a security log set to a file reports itself enabled, warns once, then drops every event. An operator believes they have an audit trail when the file is empty. It should either write the events or refuse clearly at startup.

## Reproduction

1. On Windows with Python 3.11, configure the security event log with a file sink and emit an event.

Actual: `emit` returns False and the file stays empty, while the sink still reports enabled. Expected: the event is written, or the sink doesn't report enabled. `tests/test_security_event_log.py` fails 2 tests on Windows from this.

## Cause Hypothesis

The sink sets the file's mode with `os.fchmod`, which doesn't exist on Windows Python 3.11, so it refuses to write. The public hosting docs only show POSIX paths and `stdout`.

## Acceptance Criteria

1. **Events aren't lost silently**
   **Given** a file sink on Windows
   **When** an event is emitted
   **Then** it is written with protection suited to Windows, or the app refuses the setting at startup with a clear message
2. **Linux behaviour holds**
   **Given** a file sink on Linux
   **When** an event is emitted
   **Then** the file's mode is enforced as it is today
3. **Tests cover the condition found and fixed**
   **Given** the test suite
   **When** it runs
   **Then** each operating system's behaviour has a test, and the skip story 7 adds to `test_security_event_log` is removed if the sink now writes
4. **Or: no change is needed, with proof**
   **Given** the reproduction
   **When** it is run on the current code
   **Then** the expected behavior already holds, or the report was mistaken, with the evidence recorded in Notes — this supersedes 1 to 3

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md`, Product gaps found along the way
- classification — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md`, L2

## Notes

- Assumption: Whether to write with Windows ACLs or refuse at startup is left to whoever builds this.
