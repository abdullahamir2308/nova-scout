-- Migration 007 -- Workflow 6, Send & Track.
--
-- Section 9, Workflow 6: send approved email drafts under Section 5's warm-up
-- ceiling, log each send to outreach_log, watch the same mailbox over IMAP for
-- replies and opt-outs, and queue follow-ups. This adds what that needs and
-- nothing more. Additive only -- no existing column changes meaning.

BEGIN;

-- 1. outreach_log gains the two keys the rest of Workflow 6 hangs off.
--
-- message_id: the RFC 5322 Message-ID of the message the SMTP server accepted.
-- It is how a reply is tied back to a send (a reply's In-Reply-To/References
-- carry it) and how a send is recognised when the Sent-folder mirror sees it.
--
-- NULL means the row is a CLAIM. The send path writes this row, and flips the
-- draft to 'sent', BEFORE the SMTP call -- so a crash between "sent" and
-- "logged" leaves a claim behind rather than a draft that is still 'approved'
-- and gets sent a second time on the next tick. At-most-once, on purpose: a
-- duplicate cold email is worse than a missed one. A claim counts toward the
-- daily ceiling, because the send may have gone out.
--
-- draft_id: which approved draft was sent. The learning loop (Section 7's
-- arrow from 6 back to 5) needs to know which variant earned a reply, and
-- lead_id alone cannot say once follow-ups exist.
ALTER TABLE outreach_log
  ADD COLUMN IF NOT EXISTS message_id TEXT,
  ADD COLUMN IF NOT EXISTS draft_id   BIGINT REFERENCES drafts(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS outreach_log_message_id_key ON outreach_log (message_id);
CREATE INDEX IF NOT EXISTS outreach_log_lead_id_idx ON outreach_log (lead_id);

-- 2. mailbox_sent -- the mailbox's Sent folder, mirrored.
--
-- Section 5's warm-up ceiling belongs to the MAILBOX, not to this workflow:
-- the operator's manual warm-up sends land in the same Sent folder and count
-- against the same daily limit. So the ceiling is computed from what the
-- mailbox actually sent -- the IMAP pre-flight's derivation, kept current by
-- the Sent-folder trigger in the Mailbox Watch workflow.
--
-- The send path also writes its own sends here the moment SMTP accepts them
-- (source='workflow'), so a send counts immediately rather than when the
-- mirror next notices it; the mirror then upserts the same Message-ID and only
-- sets seen_in_sent_folder. One row per message either way.
--
-- seen_in_sent_folder is the mirror's liveness proof. A workflow send that the
-- mirror has not seen after a grace period means the mirror is not reading
-- the folder -- and a dead mirror silently undercounts the operator's manual
-- sends -- so the send path stops until it catches up.
CREATE TABLE IF NOT EXISTS mailbox_sent (
  message_id          TEXT PRIMARY KEY,
  sent_at             TIMESTAMPTZ NOT NULL,
  recipients          TEXT[] NOT NULL DEFAULT '{}',
  external            BOOLEAN NOT NULL,
  lead_id             BIGINT REFERENCES leads(id) ON DELETE SET NULL,
  source              TEXT NOT NULL CHECK (source IN ('workflow', 'sent-folder')),
  seen_in_sent_folder BOOLEAN NOT NULL DEFAULT false,
  recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mailbox_sent_external_sent_at_idx ON mailbox_sent (sent_at) WHERE external;

COMMENT ON COLUMN mailbox_sent.external IS
  'At least one recipient outside the mailbox''s own domain. Only external '
  'messages count toward the warm-up ceiling -- the IMAP pre-flight''s rule.';

-- 3. mailbox_sync -- when the Sent mirror last read the folder. No row means
-- the mirror has never read it, and the send path will not guess.
CREATE TABLE IF NOT EXISTS mailbox_sync (
  folder         TEXT PRIMARY KEY,
  last_synced_at TIMESTAMPTZ NOT NULL,
  messages_seen  INTEGER NOT NULL,
  undated        INTEGER NOT NULL DEFAULT 0
);

-- 4. inbound_messages -- every INBOX message the reply watcher has processed,
-- matched to a lead or not. The primary key is what makes reprocessing a
-- no-op: the operator notification fires only on first insert, so an IMAP
-- reconnect that re-delivers a message cannot notify twice.
CREATE TABLE IF NOT EXISTS inbound_messages (
  message_id      TEXT PRIMARY KEY,
  received_at     TIMESTAMPTZ,
  from_addr       TEXT,
  subject         TEXT,
  lead_id         BIGINT REFERENCES leads(id) ON DELETE SET NULL,
  matched_by      TEXT,
  classification  TEXT NOT NULL
                  CHECK (classification IN ('reply', 'opt-out', 'auto-reply', 'bounce', 'unmatched')),
  opt_out_keyword TEXT,
  body_excerpt    TEXT,
  processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. replied_queue -- Section 9: "replied leads get their own filtered view in
-- NocoDB so they're never buried in the main queue". Read-only, like
-- review_queue since migration 006: nothing here is actioned by writing to it.
CREATE OR REPLACE VIEW replied_queue AS
SELECT i.received_at,
       i.classification,
       i.opt_out_keyword,
       l.company_name,
       l.domain,
       l.country,
       s.fit_score,
       i.from_addr,
       i.subject,
       i.body_excerpt,
       c.name   AS contact_name,
       c.email  AS contact_email,
       EXISTS (SELECT 1 FROM blocklist b WHERE b.domain = lower(l.domain)) AS domain_blocklisted,
       l.status AS lead_status,
       i.matched_by,
       i.lead_id,
       i.message_id
  FROM inbound_messages i
  JOIN leads l      ON l.id = i.lead_id
  LEFT JOIN scores s   ON s.lead_id = i.lead_id
  LEFT JOIN contacts c ON c.lead_id = i.lead_id
 WHERE i.classification IN ('reply', 'opt-out', 'bounce');

COMMENT ON VIEW replied_queue IS
  'Workflow 6: every lead that answered (reply, opt-out) or bounced, newest '
  'first in the NocoDB grid. READ-ONLY by design -- see migration 006.';

COMMIT;
