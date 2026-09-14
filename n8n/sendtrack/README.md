# Workflow 6 — Send & Track

Sends approved email drafts under Section 5's warm-up ceiling, watches the same
mailbox for replies and opt-outs, and queues follow-ups. Three workflows:

| File | Id | What it does |
|---|---|---|
| `../workflows/send.json` | `send0001` | cron every 10 min → guard → claim → SMTP → log. At most one message per tick. |
| `../workflows/mailbox-watch.json` | `mailwatch0001` | IMAP on **Sent** (mirror for the ceiling) and on **INBOX** (reply / opt-out / bounce / out-of-office). |
| `../workflows/follow-ups.json` | `followup0001` | cron every 6 h → follow-up drafts into the review queue, or mark lost. |

Same generated-not-hand-edited pattern as the other stages: the `.js` files are
the tested sources, `build_workflow.py` embeds them verbatim, and the build
parses the Master Ref and refuses to run when code and doc disagree.

```
python build_workflow.py          # regenerate the three workflow JSONs
node   test_decide.js             # 74 cases -- the send decision and every guard in it
node   test_send_result.js        # 21 cases -- what happens after the SMTP call
node   test_mailbox.js            # 49 cases -- Sent mirror, reply classification, notification
node   test_followup.js           # 13 cases -- follow-up drafts (cross-checked against the send path)
python test_drift_guards.py       # 41 cases -- each spec/wiring guard is made to fire
python provision_credentials.py   # n8n SMTP/IMAP credentials from .env (+ the two dry-run ones)
python dryrun/dryrun.py           # the real send path, end to end, into a scratch DB and an SMTP sink
python status_now.py              # read-only: what would the send path do right now?
```

Migration `postgres/migrations/007_send_and_track.sql` must be applied first.

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
  `amitrixlabs.com`, plus any claim whose SMTP outcome is unknown.

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

## Follow-ups

A template, not a model call — a follow-up adds no fact about the lead, so
there is nothing to ground. It quotes the first touch, carries the Section 5
footer (so the send path accepts it) and lands as `pending`: nothing follows up
without a human. The clock restarts at the later of the last send and the last
follow-up drafted. A rejected follow-up uses its slot. Two used and six quiet
days → `lost`.

## Credentials and identity

`provision_credentials.py` builds the n8n SMTP (`smtppro.zoho.com:587`,
STARTTLS) and IMAP credentials from `.env`; the workflow JSON never holds a
secret, and the build never reads the password (a drift-guard case proves it).
The From header and the signature every body is checked against come from
`NOVASCOUT_SENDER_*` and `NOVASCOUT_MAILBOX_ADDRESS`, baked at build time.

Reply notifications go to `NOVASCOUT_OPERATOR_EMAIL` — the operator's own inbox,
never the outreach mailbox (the build refuses that). **Unset, no notification is
sent**; replies are still recorded, visible in the `replied_queue` view.

## The dry run

`dryrun/dryrun.py` runs the real workflows through n8n against
`novascout_dryrun` — a copy of the live database with every contact address
rewritten to `…@dryrun.invalid` — and an SMTP sink (`dryrun/smtp_sink.js`) on
127.0.0.1 inside the n8n container, which captures to disk and delivers nothing.
The build generates the dry-run variants from the same node lists and proves the
only differences are credentials, Config values and the removed schedule
trigger. The live database is only read; its fingerprint is compared before and
after. Every expectation is asserted — exit 1 if any guard did not hold.

## Known gaps

- **No threading headers.** The Send Email node cannot set In-Reply-To, so
  follow-ups thread by `Re:` subject only. Replies are still matched by their
  own In-Reply-To, which carries our Message-ID.
- **STARTTLS is opportunistic** on 587: nodemailer upgrades when offered but
  does not require it. Port 465 (implicit TLS) would make TLS mandatory.
- **5.7.x policy rejections retry.** They are treated as account-level (not the
  address's fault), so a spam-block would be retried each tick.
- **Notifications count toward the ceiling** if the operator inbox is external
  — they are real sends from the domain and land in Sent.
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
