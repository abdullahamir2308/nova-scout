-- Migration 017 -- the reply assistant: thread headers, one-time approval codes,
-- and operator commands (2026-10-08).
--
-- Three things, one migration, because all three exist for the same new path:
-- a prospect replies, Sonnet 5.5 drafts an answer, and the operator approves it
-- by email before anything is sent (Section 9, Workflow 7).
--
--
-- 1. inbound_messages.in_reply_to / references_raw / thread_ids -- Workflow 6.
--
-- THE GAP, found 2026-10-08 while checking the Pharmahungary reply (lead 26,
-- message <01bd01dd562e$5fe83240$1fb896c0$@pharmahungary.com>). Classify Inbound
-- already parses In-Reply-To and References into `thread_ids` and Record Inbound
-- already matches on them -- that reply matched `thread`, which is the only way
-- it COULD have matched, because the person who answered
-- (andras.nogradi@pharmahungary.com) is not the address we wrote to
-- (businessdevelopment@pharmahungary.com). Then the headers were thrown away.
-- Nothing in the database held them, so a reply draft had nothing to thread to.
--
-- A two-message thread is recoverable without them (References is our own
-- Message-ID, which `outreach_log` has, and In-Reply-To is theirs, which
-- `inbound_messages` has). A third message is not: a client that drops its
-- References chain, or a reply to a reply, leaves a gap nothing can reconstruct.
-- So the headers are stored as received, and the reply builder prefers them.
--
-- BACKFILLED FOR ONE ROW, FROM AN OBSERVED RECORD -- see the statement at the
-- end of this file. Lead 26's headers were not lost after all: n8n keeps each
-- execution's item data, and execution 2336 (the Mailbox Watch run that
-- recorded this message) still holds the IMAP metadata, where `in-reply-to` and
-- `references` are both
-- `<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>` -- the Message-ID of
-- the first touch in `outreach_log` id 8, which is why the message matched
-- `thread`. Those are the values written, nothing inferred. Any OTHER row from
-- before this migration keeps NULL: the reply builder falls back to the two
-- Message-IDs it really has, which is correct for a two-message thread and
-- honest about a longer one.
--
--
-- 2. reply_approvals -- the one-time codes.
--
-- Nothing a model drafts in answer to a prospect is sent without the operator
-- saying so, and the operator says so by email ("APPROVE <code>"). That makes
-- the code a bearer token sitting in a mailbox, so the rules are on the table
-- and not only in the workflow that checks them -- migration 014's principle:
--
--   * `code` is the primary key, so a code cannot be issued twice;
--   * `used_at IS NOT NULL` is checked by `reply_approval_usable()` below, so a
--     replayed message cannot act twice;
--   * `expires_at` is set by the table (48 hours), not by the caller;
--   * a CHECK keeps `outcome` to approve/reject/edit and requires `used_at`
--     with it.
--
-- `kind` distinguishes the two things a code can be for: a `reply` draft, and an
-- `email-hold` -- a first touch or follow-up the claim check held after its
-- repairs, which the daily digest now lists with a code of its own so the
-- operator can approve it the same way.
--
--
-- 3. operator_commands -- what arrived, and what was done about it.
--
-- Every APPROVE/REJECT/EDIT the mailbox receives is recorded once, keyed on its
-- Message-ID, exactly as `inbound_messages` keys a prospect message -- including
-- the ones that are refused. A refusal is the interesting row: a spoofed sender,
-- a failed SPF or DKIM, an unknown code, an expired code, a reused code. The
-- operator asked for those to be "ignored and logged", and `refused_reason` is
-- the log.
--
-- Section 5's authentication rule is a CHECK as well as a code path: a row may
-- only be `accepted` when `spf_pass` and `dkim_pass` are both true and the
-- sender equals `settings.operator_email`. A workflow bug can refuse a real
-- approval; it cannot accept a forged one.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. The thread headers
-- ---------------------------------------------------------------------------

ALTER TABLE inbound_messages
  ADD COLUMN IF NOT EXISTS in_reply_to    text,
  ADD COLUMN IF NOT EXISTS references_raw text,
  ADD COLUMN IF NOT EXISTS thread_ids     text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN inbound_messages.in_reply_to IS
  'The In-Reply-To header as received, or NULL if the message carried none. '
  'Stored because a reply draft must thread to the conversation it answers.';
COMMENT ON COLUMN inbound_messages.references_raw IS
  'The References header as received, verbatim and unparsed. NULL for rows '
  'written before migration 017, except lead 26 -- see the header comment.';
COMMENT ON COLUMN inbound_messages.thread_ids IS
  'Every <...@...> token Classify Inbound found in In-Reply-To + References, '
  'deduped and in order -- the same array Record Inbound matches a lead on.';

-- Every id in the array looks like a Message-ID. Cheap, and it stops a
-- half-parsed header (a bare local part, a stray comma) reaching the References
-- line of an outbound message. A CHECK cannot hold a subquery (and `unnest` in
-- one is a subquery), so the test is an IMMUTABLE function.
CREATE OR REPLACE FUNCTION message_ids_shaped(ids text[])
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(bool_and(id ~ '^<[^<>[:space:]@]+@[^<>[:space:]]+>$'), true)
    FROM unnest(ids) AS t(id);
$$;

COMMENT ON FUNCTION message_ids_shaped(text[]) IS
  'True when every element looks like a Message-ID (<local@domain>). Empty and '
  'NULL arrays pass. Used by the inbound_messages CHECK below.';

ALTER TABLE inbound_messages
  DROP CONSTRAINT IF EXISTS inbound_thread_ids_shaped;
ALTER TABLE inbound_messages
  ADD CONSTRAINT inbound_thread_ids_shaped CHECK (message_ids_shaped(thread_ids));

-- ---------------------------------------------------------------------------
-- 2. The one-time codes
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reply_approvals (
  code                text PRIMARY KEY,
  kind                text NOT NULL,
  draft_id            bigint NOT NULL REFERENCES drafts(id),
  lead_id             bigint NOT NULL REFERENCES leads(id),
  inbound_message_id  text REFERENCES inbound_messages(message_id),
  issued_at           timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz NOT NULL,
  reminded_at         timestamptz,
  used_at             timestamptz,
  used_by_message_id  text,
  outcome             text,

  CONSTRAINT reply_approvals_kind
    CHECK (kind IN ('reply', 'email-hold')),

  -- A reply code belongs to the prospect message it answers; an email-hold code
  -- is about a draft nobody replied to.
  CONSTRAINT reply_approvals_inbound_for_reply
    CHECK ((kind = 'reply') = (inbound_message_id IS NOT NULL)),

  -- The code is at least 10 characters of unambiguous alphabet, upper case, so
  -- it survives a mail client's capitalisation and a human's retyping. The
  -- alphabet (no I, L, O, U, 0, 1) is in code_reply_code.js; this only pins the
  -- shape so a short or punctuated code can never be stored.
  CONSTRAINT reply_approvals_code_shaped
    CHECK (code ~ '^NS-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{10}$'),

  -- 48 hours, set by the table. A caller that computes its own window cannot
  -- widen it.
  CONSTRAINT reply_approvals_window
    CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '48 hours'),

  CONSTRAINT reply_approvals_outcome
    CHECK (outcome IS NULL OR outcome IN ('approved', 'rejected', 'edited')),

  -- An outcome and a use are the same event.
  CONSTRAINT reply_approvals_used_together
    CHECK ((used_at IS NULL) = (outcome IS NULL)),
  CONSTRAINT reply_approvals_used_by
    CHECK ((used_at IS NULL) = (used_by_message_id IS NULL))
);

COMMENT ON TABLE reply_approvals IS
  'One-time codes the operator quotes back by email to approve, reject or edit '
  'a draft (Section 9, Workflow 7). Each works once and expires after 48 hours; '
  'both rules are CHECKs and reply_approval_usable() below, not only workflow code.';

-- At most one unused, unexpired code per draft: a second reminder or a re-run
-- must not mint a second valid code for the same thing.
CREATE UNIQUE INDEX IF NOT EXISTS reply_approvals_one_open_per_draft
  ON reply_approvals (draft_id) WHERE used_at IS NULL;

CREATE INDEX IF NOT EXISTS reply_approvals_open
  ON reply_approvals (expires_at) WHERE used_at IS NULL;

-- The one definition of "this code may be acted on now", for every reader --
-- the same role auto_approve_email_enabled() plays for migration 014.
CREATE OR REPLACE FUNCTION reply_approval_usable(p_code text)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM reply_approvals a
     WHERE a.code = p_code
       AND a.used_at IS NULL
       AND a.expires_at > now()
  );
$$;

COMMENT ON FUNCTION reply_approval_usable(text) IS
  'True only for a code that exists, has never been used, and has not expired. '
  'Every reader uses this rather than re-deriving the window.';

-- ---------------------------------------------------------------------------
-- 3. What the operator sent, accepted or refused
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS operator_commands (
  message_id      text PRIMARY KEY,
  received_at     timestamptz NOT NULL,
  from_addr       text NOT NULL,
  subject         text,
  command         text,
  code            text,
  spf_pass        boolean NOT NULL DEFAULT false,
  dkim_pass       boolean NOT NULL DEFAULT false,
  sender_ok       boolean NOT NULL DEFAULT false,
  accepted        boolean NOT NULL DEFAULT false,
  refused_reason  text,
  replacement     text,
  draft_id        bigint REFERENCES drafts(id),
  processed_at    timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT operator_commands_command
    CHECK (command IS NULL OR command IN ('APPROVE', 'REJECT', 'EDIT')),

  -- Section 5 / Section 9, as a constraint and not only as a code path: a
  -- command is accepted ONLY from the operator's exact address with SPF and
  -- DKIM both passing. A bug in the workflow can refuse a real approval; it
  -- cannot accept a forged one.
  CONSTRAINT operator_commands_authenticated
    CHECK (NOT accepted OR (spf_pass AND dkim_pass AND sender_ok
                            AND command IS NOT NULL AND code IS NOT NULL)),

  -- Every refusal says why; an acceptance has nothing to say.
  CONSTRAINT operator_commands_reason
    CHECK (accepted = (refused_reason IS NULL)),

  -- EDIT carries the replacement text; the other two do not.
  CONSTRAINT operator_commands_replacement
    CHECK (replacement IS NULL OR command = 'EDIT')
);

COMMENT ON TABLE operator_commands IS
  'Every APPROVE/REJECT/EDIT the mailbox received, accepted or refused, keyed on '
  'Message-ID so a re-delivered message acts once. A refused row is the audit '
  'trail the operator asked for: spoofed sender, failed SPF/DKIM, unknown, '
  'expired or reused code.';

CREATE INDEX IF NOT EXISTS operator_commands_refused
  ON operator_commands (received_at DESC) WHERE NOT accepted;

-- ---------------------------------------------------------------------------
-- 4. A reply draft is a conversation, not cold outreach
-- ---------------------------------------------------------------------------
--
-- Section 9, Workflow 7: the warm-up ceiling and the recipient's business hours
-- do not apply to an answer to a message they sent us; the blocklist still
-- does. Send tells the two apart by the variant prefix, so the shape of that
-- prefix is pinned here as well as in the workflow -- a draft that merely
-- claims to be a reply must still look like one.
ALTER TABLE drafts
  DROP CONSTRAINT IF EXISTS drafts_reply_variant_shaped;
ALTER TABLE drafts
  ADD CONSTRAINT drafts_reply_variant_shaped CHECK (
    coalesce(variant, '') NOT LIKE 'reply/%' OR channel = 'email'
  );

-- ---------------------------------------------------------------------------
-- 5. The one backfill, from an observed record
-- ---------------------------------------------------------------------------
--
-- See section 1's comment. The values come from n8n execution 2336's stored
-- IMAP metadata, not from inference. Guarded so a database that never had this
-- row, or whose row has since been written properly, is untouched.
UPDATE inbound_messages
   SET in_reply_to    = '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>',
       references_raw = '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>',
       thread_ids     = ARRAY['<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>']
 WHERE message_id = '<01bd01dd562e$5fe83240$1fb896c0$@pharmahungary.com>'
   AND in_reply_to IS NULL
   AND EXISTS (SELECT 1 FROM outreach_log o
                WHERE o.message_id = '<8784d79c-45c4-c419-65e6-45da04fe1b50@amitrixlabs.com>');

COMMIT;
