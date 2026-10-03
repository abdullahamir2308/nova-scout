# Workflow 6 — Send & Track

Sends approved email drafts under Section 5's warm-up ceiling, watches the same
mailbox for replies and opt-outs, queues follow-ups, and checks that the mailbox
watch is still listening, and tells the operator once a day what happened.
Five workflows:

| File | Id | What it does |
|---|---|---|
| `../workflows/send.json` | `send0001` | cron every 10 min → guard → claim → SMTP → log. At most one message per tick. |
| `../workflows/mailbox-watch.json` | `mailwatch0001` | IMAP on **Sent** (mirror for the ceiling) and on **INBOX** (reply / opt-out / bounce / out-of-office). |
| `../workflows/follow-ups.json` | `followup0001` | cron every 30 min → follow-up composed by the drafting model, checked by the drafting rules, into the review queue as `pending`; or mark lost. |
| `../workflows/imap-health.json` | `imaphealth0001` | cron every 20 min → IMAP checker → has Mailbox Watch missed anything? → alert the operator. |
| `../workflows/daily-digest.json` | `digest0001` | cron every 30 min → once per operator day, from 08:00 PKT: what auto-approved, what was sent, what is held and why → the operator. |

Same generated-not-hand-edited pattern as the other stages: the `.js` files are
the tested sources, `build_workflow.py` embeds them verbatim, and the build
parses the Master Ref and refuses to run when code and doc disagree.

```
python build_workflow.py          # regenerate the five workflow JSONs
node   test_decide.js             # 74 cases -- the send decision and every guard in it
node   test_send_result.js        # 21 cases -- what happens after the SMTP call
node   test_mailbox.js            # 71 cases -- Sent mirror, reply classification, positive-signal flag, notification
node   test_followup.js           # 83 cases -- composed follow-ups: the request, every section 8 rule, the frame (cross-checked against the send path), the approval context
node   test_digest.js             # 19 cases -- the Daily Digest: when it is due, what it says
python test_followup_mutations.py # breaks each follow-up rule in turn; test_followup.js must fail every time
node   test_health.js             # 33 cases -- IMAP Health: problems, when it alerts, what it says
python test_imap_health.py        # 21 cases -- the checker's answer; Message-ID parity with Mailbox Watch
python test_drift_guards.py       # 92 cases -- each spec/wiring guard is made to fire
python provision_credentials.py   # n8n SMTP/IMAP credentials from .env (+ the two dry-run ones)
python sync_settings.py           # the operator's notification address, .env -> the settings table
python imap_preflight.py          # read-only: IMAP works, and the warm-up state from the real Sent folder
python dryrun/dryrun.py           # the real send path, end to end, into a scratch DB and an SMTP sink
python dryrun/health_dryrun.py    # IMAP Health end to end: real checker, scratch DB, SMTP sink
python dryrun/approval_dryrun.py  # auto-approval end to end: Drafting, Follow-Ups, Send, Digest; real claim check
python status_now.py              # read-only: what would the send path do right now?
```

Migrations `postgres/migrations/007_send_and_track.sql`, `008_settings.sql`,
`009_mailbox_health.sql` and `014_auto_approval.sql` must be applied first, `sync_settings.py` run once, and
the `imap-health` service running (`docker compose up -d imap-health`).

## The warm-up ceiling belongs to the mailbox

Section 5's table is a daily limit for one mailbox, and the operator's manual
warm-up sends land in the same Sent folder as the workflow's. So the ceiling is
computed from what the mailbox actually sent — the IMAP pre-flight's derivation,
kept current:

- **Warm-up starts** on the sender-day of the first send with an external
  recipient; week = ⌊days since / 7⌋ + 1. An empty history is "not started", and
  the first send is day 1 of week 1.
- **The day** is the sender's (Asia/Karachi, docker-compose's `GENERIC_TIMEZONE`),
  so the count cannot straddle two days. Each tick uses one clock value throughout.
- **What counts**: every message in the Sent folder with a recipient outside
  `amitrixlabs.com` other than the operator's notification address, plus any
  claim whose SMTP outcome is unknown. A reply notification — anything sent
  only to the operator — warms nothing, the same as a note to a colleague.

The Mailbox Watch workflow's Sent trigger reads the whole folder on every
activation into `mailbox_sent`, then each new message as it is filed. The send
path writes its own sends there the moment SMTP accepts them, so a send counts
at once; the mirror only marks the same Message-ID `seen_in_sent_folder`.

**It fails closed.** The send path refuses to send if the mirror has never read
the folder (`sent-mirror-never-ran`), or if one of its own sends has not shown up
there after 15 minutes (`sent-mirror-behind`) — a dead mirror would undercount
the manual sends with no error anywhere. Consequence worth knowing: the Sent
trigger only fires once the folder holds a message, so **nothing is sent until
at least one message is in Sent and Mailbox Watch is published.**

The ceiling is checked twice. Decide Send refuses at the ceiling; then Claim
Send re-counts it in SQL, under a Postgres advisory lock taken in a *separate
statement* — the Postgres node runs the text as one implicit transaction, and
under READ COMMITTED only a later statement's snapshot sees a concurrent claim
that committed while it waited. Inside one statement, two overlapping ticks
could both see 4 of 5.

## Claim, then send — at most once

Claim Send flips the draft to `sent` and writes an `outreach_log` row with no
Message-ID **before** the SMTP call. A crash between "sent" and "logged" leaves a
claim, not an approved draft that goes out again on the next tick; a duplicate
cold email is worse than a missed one. The claim counts toward the ceiling until
resolved. After the call:

| SMTP outcome | What happens |
|---|---|
| accepted, Message-ID returned | Confirm: Message-ID onto the claim, lead → `sent`, send into `mailbox_sent` |
| auth / TLS / DNS / timeout / 4xx | Revert: claim removed, draft back to `approved`, retried next tick |
| 5xx naming the recipient | Revert: claim removed, draft back to `pending` tagged `+smtp-rejected` — to a human, not the retry loop |

The Send Email node has no retry: a timeout after the server accepted would send twice.

## What every send is checked against

Selected by SQL: `channel = 'email'` and `status = 'approved'`. Then, per draft,
in Decide Send (and the ones marked ★ again inside Claim Send):

- ★ contact `verified` (Section 9, Workflow 3b: "this is the flag a send gates on"), valid address
- ★ lead domain and recipient domain not on `blocklist` (exact or subdomain)
- ★ no reply and no bounce on the lead; ★ first touch only if never emailed (by the workflow or by hand)
- not a low-context note, even if a human approved it by mistake; has a subject
- carries Section 5's opt-out sentence **verbatim**, immediately followed by the
  current signature — a body signed by a previous identity is refused
  (`stale-signature`) rather than sent under the wrong name
- at most one URL (Section 5)
- follow-ups only after a first touch, at most two
- ★ the body is unchanged since Decide read it (an edit mid-tick aborts the claim)

Then **the recipient's business hours**: 09:00–17:00 local, Monday–Friday
(Egypt Sunday–Thursday), on a fixed standard-time offset per Section 12 country.
No DST, by the operator's decision — Poland, Czech Republic, Hungary, Romania and
Egypt run an hour later than the table in summer. Then pacing: 20 minutes since
the mailbox's last external send, then a 40% coin flip per tick.

## LinkedIn is never sent

Section 6, three layers deep: the query never selects a LinkedIn draft; Decide
Send throws — stopping the tick — if one ever arrives; Claim Send refuses any
draft whose channel is not email. The build refuses if either query loses its
`channel = 'email'` filter.

## Replies and opt-outs

Classification reads only **what they wrote above the quoted thread**. Every
reply quotes our email, and our email says "reply 'no'" — classify the whole
body and every reply is an opt-out. Quote headers are recognised in the leads'
languages (`wrote:`, `escribió:`, `escreveu:`, `yazdı:`, `napisał:`, `írta:`,
`a scris:` …), plus Outlook's `From:/Sent:` block, and the opt-out sentence is
stripped again in case a client quoted it without a marker.

- **Opt-out**: `unsubscribe`, `remove` or `stop` anywhere in their text; `no`
  only as the first word ("No, thanks" — not "we have no chatbot"). The first-word
  "no" also in Portuguese, Polish, Turkish, Hungarian and Romanian. Czech "ne" is
  deliberately left out: it opens ordinary Turkish questions ("Ne zaman…?").
  An opt-out blocklists the lead's domain (and the sender's, if it is a
  different company domain) permanently, and the notification says which keyword matched.
- **Auto-reply** (Auto-Submitted header or an out-of-office subject) and
  **bounce** (mailer-daemon, NDR subject or delivery-status report) are recorded
  but are not replies: an out-of-office must not kill the follow-up sequence.
- Matching, strongest first: our Message-ID in In-Reply-To/References, the
  contact's address, the address a bounce names, the lead's domain (never a
  freemail domain).
- `inbound_messages` is keyed on Message-ID, so a re-delivered message changes
  nothing and notifies nobody twice.

## The operator notification: positive/neutral signal, and assets

`Detect Positive Signal` sits between Record Inbound and Build Notification.
It is deterministic (build rule 3, no model) and reads only `classification`
and `body_excerpt` from Record Inbound's row — it does not touch, and cannot
touch, Classify Inbound's classification. Only `classification === 'reply'` is
scored: bounce/auto-reply/opt-out have already been decided by then, so "Yes,
please remove me." stays an OPT-OUT on "remove" and its "yes" is never read as
a positive signal — the notification shows no Signal line at all for it.
`positive` is a keyword match (`yes`, `sure`, `interested`, `sounds good`,
`send it`, …) against the reply's own text; anything else is `neutral`.

Build Notification's email already carries the reply text inline
(`body_excerpt`, "What they wrote"); it now also shows `Signal: POSITIVE
(matched "…")` or `Signal: NEUTRAL` for a reply, and an `Assets:` section.
Assets are a `{ label, url }` list in `code_notify.js` — today just the
one-pager; no recording link exists or is planned, so adding one later is one
more entry in that list, not a template rebuild.

## Follow-ups

**Composed since 2026-10-02 (drafting skill v3 §8), not a template.** Due: six
quiet days after the last send or follow-up; at most two, then `lost`. A
rejected follow-up uses its slot.

```
Find Due Follow-Ups -> Build Follow-Up -> Needs Model? -> Claude Follow-Up -> Assemble Follow-Up -> Drop Failed Generations -> Write Follow-Up
                                                      \-> (mark-lost) ------------------------------------------------------------> Write Follow-Up
```

- **Build Follow-Up** (`code_followup.js`) sends the drafting model (Master Ref
  §3, same request parameters) the first email exactly as sent — minus greeting,
  opt-out and signature — plus the approved claims: for #1, only the angles and
  benefits the first email did not use, and no benefit sharing a capability with
  one it used; for #2, only the asks. Nothing else about the lead reaches the
  model, so build rule 6 holds by construction. An email written under an older
  claims list (leads 7, 91, 104 were emailed under v2) cannot be read off its
  codes, so the model reports `first_email_covers` and the added line is checked
  against it.
- **Assemble Follow-Up** is drafting's `code_assemble.js` — everything above its
  `// Node body` marker, verbatim — followed by `code_followup_assemble.js`. So a
  follow-up runs the first touch's own rule functions (ask, product name, "AI",
  sponsor, every forbidden claim, `claim-repeat`, links, areas, unconfirmed
  claims), plus section 8's: #1 40–70 words and exactly one new angle or benefit
  (`short`, `long`, `fu-no-new-claim`, `fu-repeat`), #2 at most 40 words and no
  new claim (`fu-new-claim`). The build refuses if drafting stops defining a
  function this body calls, or if node-body code moves above the marker. **Keep
  `code_assemble.js`'s rules section free of n8n reads (`$(`, `$input`).**
- The greeting, the Section 5 opt-out, the signature and the quoted first email
  are appended, never generated; the subject is `Re:` + the first subject.
  `test_followup.js` runs every composed follow-up through Send's real
  `ineligibility()`.
- **Auto-approval (migration 014).** A composed follow-up then goes through
  Workflow 4's own Approval Gate and claim check (`../drafting/code_approval.js`
  and `code_approval_apply.js`, embedded verbatim): with the flag on, no rule
  tag, every claim code confirmed, and a second Sonnet 5.5 call finding every
  claim supported by a confirmed line and every prospect fact in the enrichment
  record, it is written `approved` (`approved_by = 'auto'`) and Send sends it in
  the recipient's business hours like any approved draft. Otherwise it is
  written `pending` with `hold_reason`. Only the note is checked -- the subject
  is `Re:` + one already sent, and the quoted first email already went out. The
  enrichment record reaches the checker only, never the composing model. The
  record holds no trial (Workflow 4 stores none), so a follow-up naming one is
  held for a person.
- A failed call writes nothing; the lead is due again next run. Write Follow-Up
  dedupes on the follow-up **number** (`follow-up-1/BEN-DECK.A1+...` and a
  pre-2026-10-02 bare `follow-up-1` are the same slot).

**Why every 30 minutes, not every 6 hours.** On 2026-10-02 leads 7, 91 and 104
had gone 10 days without a follow-up against a 6-day rule. Two causes, both
measured: (1) the stack was down — Docker Desktop has `AutoStart: false`, and
between 2026-09-23 02:40 and 2026-10-01 23:05 PKT n8n executed nothing at all,
although Windows was up 2026-09-27 15:03 → 09-28 03:06 and 09-28 23:21 → 10-01
12:00; (2) the one 6-hour tick n8n was up for after the due date, 2026-10-02
00:00:29 PKT, was **skipped by n8n itself**: the Schedule Trigger's
`recurrenceCheck` (n8n 2.35.7, `GenericFunctions.js`) compares the **clock hour**
of the last run, persisted in `staticData` (`recurrenceRules: [0]` from 09-23
00:00 PKT), with `(hour - last + 24) % 24 >= 6` — and 0 − 0 is 0. Replaying the
installed function with that state gives SKIPPED at 00:00:29 and FIRES at 06/12/18.
Minutes intervals are counted on elapsed time there, so they are safe; the build
now refuses any hours/days/weeks interval above 1 in any Workflow 6 schedule.

## The Daily Digest

Auto-approval means drafts reach prospects that no person read, so the operator
gets one email a day saying what happened (`code_digest.js`, migration 014's
`digest_log`):

- **AUTO-APPROVED** -- every draft the workflows approved themselves since the
  last digest, and what became of it. Each goes out in its recipient's
  business hours; rejecting it in the review queue stops it.
- **SENT** -- every email SMTP accepted, and who approved it (`auto`/`human`).
- **HELD FOR A PERSON** -- every pending draft, new ones marked, each with its
  `hold_reason`: the exceptions queue.

It runs every 30 minutes and sends at the first tick at or after 08:00 on the
operator's clock (before India, the earliest recipient, opens at 08:30 PKT),
once per operator day; it covers from where the last digest stopped, so a day
the host was off is folded into the next one. Recorded only once SMTP accepted
it. It goes only to `settings.operator_email`, so -- like a reply
notification -- it uses no warm-up slot. No address, no digest.

## Is Mailbox Watch still listening?

An IMAP trigger only proves it is alive when it fires. When its connection
drops, n8n unloads the workflow's triggers and retries with a backoff that
doubles up to 24 hours -- while the database still says "active", "published"
and `triggerCount=2`. Master Ref Section 9 has the record of the drops in the
first day.

**IMAP Health** checks every 20 minutes:

1. **Can a fresh read-only IMAP session log in?** `imap_preflight.py` does
   this -- the same connection logic, unchanged. The n8n image has no Python
   and n8n 2.x excludes the Execute Command node, so it runs in the
   `imap-health` container (`imap_health_server.py`, one internal URL:
   `http://imap-health:8765/check`). That container gets only the IMAP
   settings, mounts its code read-only, runs as `nobody` on a read-only
   filesystem, and publishes **no port**. The build refuses a port.
2. **Has Mailbox Watch missed anything?** With `--ids-json`, the pre-flight
   also lists the Message-ID of every INBOX and Sent message from the last 48
   hours. Anything that landed more than 15 minutes ago without a row in
   `inbound_messages` (INBOX, from Mailbox Watch's first run) or `mailbox_sent`
   (Sent) was missed. A login only proves the mailbox is reachable. This proves
   the triggers are listening. `test_imap_health.py` checks that the pre-flight
   derives each key exactly as both Code nodes do. If it didn't, every message
   would look missed.

| Problem | Meaning |
|---|---|
| `checker-unreachable` | nothing answered at the checker URL -- nothing was checked |
| `imap-failed` | the fresh IMAP session failed (stage + the pre-flight's reason) |
| `inbox-missed` | INBOX mail Mailbox Watch has not recorded -- listed, for a by-hand opt-out check |
| `sent-missed` | Sent mail the mirror has not recorded -- the warm-up ceiling undercounts |

**Alerting:** nothing on one failing check. A host waking from sleep can tick
the check before Mailbox Watch reconnects, and mail from the sleep looks missed
for those seconds. After two failing checks in a row, one email goes to
`settings.operator_email`. Then a reminder every 6 hours, a new email if the
problem changes, and one when it clears. State lives in `mailbox_health`. An
alert counts as sent only once SMTP accepts it. It goes only to the operator,
so it uses no warm-up slot.

**It cannot report its own absence:** when n8n or the host is down, it does
not run either. Mail that lands then is recorded on the next start.

## Credentials and identity

`provision_credentials.py` builds the n8n SMTP (`smtppro.zoho.com:587`,
STARTTLS) and IMAP credentials from `.env`; the workflow JSON never holds a
secret, and the build never reads the password (a drift-guard case proves it).
The From header and the signature every body is checked against come from
`NOVASCOUT_SENDER_*` and `NOVASCOUT_MAILBOX_ADDRESS`, baked at build time.

Reply notifications go to the operator's own inbox, never the outreach mailbox.
The address is **runtime data, not a build constant**: `sync_settings.py` writes
`NOVASCOUT_OPERATOR_EMAIL` from `.env` into the `settings` table (migration 008;
it refuses the outreach mailbox), and Mailbox Watch reads it there — Load
Settings for the Sent mirror, Record Inbound for the notification. Changing it
needs a re-sync, not a rebuild. **Unset, no notification is sent**; replies are
still recorded, visible in the `replied_queue` view.

Not an n8n environment variable, on purpose: n8n 2.x blocks `$env` in Code nodes
and expressions unless `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, which would expose
the whole container environment (`N8N_ENCRYPTION_KEY` included) to every Code
node. The build refuses any literal email address in the shipped JSON except
the sender's own From.

## The dry run

`dryrun/dryrun.py` runs the real workflows through n8n against
`novascout_dryrun` — a copy of the live database with every contact address
rewritten to `…@dryrun.invalid` — and an SMTP sink (`dryrun/smtp_sink.js`) on
127.0.0.1 inside the n8n container, which captures to disk and delivers nothing.
The build generates the dry-run variants from the same node lists and proves the
only differences are credentials, Config values and the removed schedule
trigger. The live database is only read; its fingerprint is compared before and
after. Every expectation is asserted — exit 1 if any guard did not hold.

The scratch copy's operator address is `operator@dryrun.invalid`, never the real
one. Scenario H fires a real reply notification into the sink, feeds its Sent
copy back through the mirror, and checks the send path's remaining count did not
move; its control run (no operator address configured) shows the same message
would otherwise have taken the day's last slot. Afterwards the scratch databases
are dropped and the dry-run workflows deleted from n8n (`--keep` leaves both).

## Known gaps

- **No threading headers.** The Send Email node cannot set In-Reply-To, so
  follow-ups thread by `Re:` subject only. Replies are still matched by their
  own In-Reply-To, which carries our Message-ID.
- **STARTTLS is opportunistic** on 587: nodemailer upgrades when offered but
  does not require it. Port 465 (implicit TLS) would make TLS mandatory.
- **5.7.x policy rejections retry.** They are treated as account-level (not the
  address's fault), so a spam-block would be retried each tick.
- **Zoho filing SMTP sends into Sent is assumed, not yet observed** — no live
  send has happened. If it does not, the first send will stop the send path
  with `sent-mirror-behind` (fail closed), not over-send.

## Two things that bit during the build

- **The Send Email node appends "This email was sent automatically with n8n"
  and a link by default** at typeVersion 2.1 — verified in the installed
  `send.operation.js`. `appendAttribution: false` is set explicitly and asserted
  at build time.
- **`docker cp` writes as root.** The first credential import copied the secrets
  file in with `docker cp`; the container's `node` user could read it but not
  delete it, and it outlived the import in `/tmp`. The provisioner now streams it
  over stdin as `node`, with `umask 077`.
