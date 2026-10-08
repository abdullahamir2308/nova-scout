# Standing rules for every task in this queue

These rules apply in addition to whatever the task below asks for.

- Never send, schedule, or approve sending any email or message to a real
  prospect or contact. Use the project's dry-run harness only.
- Never print, log, or write out secrets: `.env` values, API keys,
  passwords, tokens, or any other credential.
- n8n workflows can only be published from the n8n UI, never from the CLI
  or by editing the database directly. If this task imports or changes a
  workflow, leave it unpublished, list it under `publish in the UI: <names>`
  in your progress report, and use `STATUS: NEEDS-HUMAN`. Use
  `STATUS: NEEDS-HUMAN` for any other step that only a human can finish.
- Before making any decision that affects the project's architecture or
  behavior, read the relevant section(s) of `NovaScout_MasterRef.md` first,
  and update that file with any decision you make during this task.
- Do not edit any file under `prompts/` or `progress/` other than your own
  progress report. Only commit the files this task changed, naming them
  explicitly (never `git add -A` or `git add .`), and never `git push`.
