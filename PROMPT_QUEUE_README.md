# Unattended prompt queue (run-all.ps1)

Runs a numbered sequence of Claude Code tasks, unattended, each in its own
fresh `claude -p --dangerously-skip-permissions` session. Windows/PowerShell
only.

## Layout

- `prompts/NN-name.md` — one task per file, run in filename order. Only
  files matching `NN-*.md` (two digits) count as tasks.
- `prompts/_preamble.md` — not a task. Its content is prepended to every
  task's prompt. Holds the standing rules (no live email, no secrets, n8n
  publish-in-UI-only, read/update `NovaScout_MasterRef.md`, don't touch
  other tasks' files, commit only your own work, never push).
- An optional first line in a task file, `model: opus` or `model: sonnet`,
  picks the model for that task (default `sonnet`). That line is stripped
  before the prompt is sent.
- `progress/NN-name.md` — one report per task, written by the task itself.
  First line is exactly `STATUS: DONE`, `STATUS: BLOCKED`, or
  `STATUS: NEEDS-HUMAN`, followed by `## What was done`, `## Files changed`,
  `## Problems`, `## Human actions needed`, `## Next task should know`.
  Under 300 words, no secrets, no personal email addresses. These are
  hand-off notes between tasks and for you to skim — decisions still belong
  in `NovaScout_MasterRef.md`.
- `logs/` — full stdout/stderr of every task run, one file per task named
  `<timestamp>-<task>.log`. Gitignored.

## Running it

```
powershell -ExecutionPolicy Bypass -File .\run-all.ps1
```

Flags:
- `-SkipPreflight` — skip the Docker/Ollama checks (otherwise the run
  refuses to start if either isn't up).
- `-TaskTimeoutMinutes <n>` — per-task time limit before the task's
  `claude` process (and its children) is killed. Default 120.

Each task is told, before its own instructions, to read every file in
`progress/` first, and after its own instructions, exactly how to format
its report. `run-all.ps1` prints each task's name, model, and start/finish
time as it goes, and a summary table at the end.

**Resuming:** a task whose report already says `STATUS: DONE` is skipped.
Anything else (no report yet, or a report that says `BLOCKED`/
`NEEDS-HUMAN`) is (re-)run. So re-running `run-all.ps1` after a stop picks
up where it left off.

**Stopping:** the queue stops immediately on a non-zero exit code from
`claude`, a missing report, a report with no valid `STATUS:` line, or any
`STATUS` other than `DONE`. It prints which task stopped, why, and the
report's `## Human actions needed` section, then exits with code 1. On a
full run with no stop, it exits 0.

The host is kept awake (via `SetThreadExecutionState`) for the duration of
the run and released when it exits, including on failure.

## Adding real tasks

Number real tasks from **10** upward — 01-09 are reserved for the
infrastructure's own self-tests (see below), so a stray leftover test
prompt can't collide with real work.

## Self-tests (already run once; not part of normal operation)

`01-test1.md`/`02-test2.md` prove two tasks run as separate sessions, each
produces a report, the second reads the first's report, and logs are
written. A temporary `03-*.md` prompt whose task writes `STATUS:
NEEDS-HUMAN` on purpose proves the stop rule, and that a re-run still skips
the already-`DONE` tasks. The test prompts, their reports, and their logs
are deleted once the check passes — if you ever see `prompts/01-test1.md`
or similar lying around again, it's a leftover from re-running the check,
not a real task, and is safe to delete.
