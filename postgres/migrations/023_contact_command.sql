-- Migration 023 -- the CONTACT operator command (2026-10-28).
--
-- Master Ref Section 9, Workflow 7. The operator gives a lead its email address
-- by mail, one line per lead, no one-time code:
--
--   CONTACT <lead domain> <email address> [name]
--
-- WHY. On 2026-10-24 the operator answered the "no address" hold on JSS Medical
-- Research with "EDIT <code> Send to this email mailto:bdteam@jssresearch.com".
-- EDIT replaces a draft's text, so draft 350 was approved with that instruction
-- as its whole message; only the missing address kept it from being sent. There
-- was no way to supply an address by mail. CONTACT is that way, and EDIT on a
-- held cold email now refuses anything that is not a whole email
-- (code_cold_email.js; Send applies the same rule).
--
-- What CONTACT does is Apply Operator Command's (n8n/sendtrack/build_workflow.py):
-- for each line that names a lead never emailed, at scored / contact_found /
-- drafted, with neither domain blocklisted, it upserts the contact (verified =
-- true by the operator's word), clears the lead's contact_attempts row and moves
-- the lead to contact_found. Drafting then writes the first touch through the
-- normal gate. This migration only lets operator_commands record it:
--
--   * command may be 'CONTACT';
--   * an accepted CONTACT needs no code (it names leads, not a draft) -- SPF,
--     DKIM and the operator's own address are still required, as for every
--     command: a workflow bug can refuse a real command, never accept a forged one;
--   * contact_lines keeps every line as judged: domain, address, name, lead,
--     and why a line was not applied.
--
-- Additive and idempotent.

BEGIN;

ALTER TABLE operator_commands
  ADD COLUMN IF NOT EXISTS contact_lines jsonb;

ALTER TABLE operator_commands DROP CONSTRAINT IF EXISTS operator_commands_command;
ALTER TABLE operator_commands ADD CONSTRAINT operator_commands_command
  CHECK (command IS NULL OR command IN ('APPROVE', 'REJECT', 'EDIT', 'CONTACT'));

ALTER TABLE operator_commands DROP CONSTRAINT IF EXISTS operator_commands_authenticated;
ALTER TABLE operator_commands ADD CONSTRAINT operator_commands_authenticated
  CHECK (NOT accepted OR (spf_pass AND dkim_pass AND sender_ok
                          AND command IS NOT NULL
                          AND (code IS NOT NULL OR command = 'CONTACT')));

ALTER TABLE operator_commands DROP CONSTRAINT IF EXISTS operator_commands_contact_lines;
ALTER TABLE operator_commands ADD CONSTRAINT operator_commands_contact_lines
  CHECK (contact_lines IS NULL
         OR (command = 'CONTACT' AND jsonb_typeof(contact_lines) = 'array'));

COMMENT ON TABLE operator_commands IS
  'Every APPROVE/REJECT/EDIT/CONTACT the mailbox received, accepted or refused, keyed on '
  'Message-ID so a re-delivered message acts once. A refused row is the audit '
  'trail the operator asked for: spoofed sender, failed SPF/DKIM, unknown, '
  'expired or reused code. CONTACT (migration 023) carries no code.';
COMMENT ON COLUMN operator_commands.contact_lines IS
  'Migration 023: a CONTACT message''s lines as judged -- line, domain, email, name, lead_id, '
  'company, company_type, refused (NULL when that line was applied).';

COMMIT;
