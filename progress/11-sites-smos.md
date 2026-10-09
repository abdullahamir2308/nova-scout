STATUS: DONE

## What was done

The 2026-10-09 attempt hit the usage limit after writing MasterRef but before acting on
it, so I checked each step against the live system.

**Already complete, not redone:** step 0's two prerequisites, steps 1-6, §4/§9/§12 —
21 suites green (1,100+ checks), `trialsites.json` and the site one-pager regenerate
identically, claim notes spot-checked against the Nova Agent Kit source.

**Done this run**, because §13 claimed two things that had not happened: migration `019`
was absent from the live database — applied, re-applied to prove the no-op (20 CRO claims
untouched, 18 site/SMO claims seeded inactive and unconfirmed); n8n held pre-sites
versions of all six workflows and no `trialsites0001` — all seven imported, verified,
unpublished. Added `n8n/sites/test_drift_guards.py` (29 cases): the generator had 28
build-time refusals and no suite, unlike every other stage. Re-ran step 7.

Funnel: 506 leads — 271 scored, 157 disqualified, 44 sent, 33 drafted, 1 replied; nothing
mid-pipeline; **15 approved unsent = 0.75 days at 20/day.** Dry run: 200 candidates
chosen from 30,034 rows; 20 lookups $0.3757; 10 new leads; 4 site drafts, all held
`unconfirmed-claim`; 26/26 checks, $0.47. Detail and samples in §9.

## Files changed

`NovaScout_MasterRef.md` (§9, §13); `n8n/sites/` (new `test_drift_guards.py`, guard fix in
`build_workflow.py`, empty-run guard in `dryrun_sites.py`); plus 2026-10-09's uncommitted
sites work — migration `019`, `trialsites.json`, six changed workflows,
`n8n/{sites,enrichment,scoring,drafting,sendtrack}/*`, `nova-one-pager-sites.docx`.
Left alone: `README.md`, `run-all.ps1` (pre-existing, unrelated).

## Problems

Two defects, fixed. The alias-table guard was unreachable: `.index("};")` searched from
the file start, so a renamed table raised `ValueError` first (`trialsites.json` is
byte-identical after the fix). And `dryrun_sites.py` could print "26/26 passed" having
harvested nothing — a stale `n8n execute` held broker port 5690, and every later check
passes on an empty database. That made my first attempt look like a pass.

## Human actions needed

1. **Publish the seven workflows**, Mailbox Watch first, Trialsites last and only after 3.
2. **Tick `active` then `confirmed` on the 18 site/SMO claims.** Both lines saying
   "operator to confirm" are fair: `capture_sponsor_lead`'s field is described as the
   *sponsor's* organisation, and `capture_investigator_registration` still names
   NoblePath's network in code.
3. **Decide the Trialsites licence question** ("free for non-commercial use").
4. **Push `main`** or the site one-pager link stays a 404.

## Next task should know

Nothing site-related is live: no site lead enters the pipeline until the workflows are
published, and no site draft auto-approves until the claims are confirmed. Scratch DB
`novascout_dryrun` and the five `dry*0001` workflows were kept for inspection.
