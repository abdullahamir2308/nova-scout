-- Migration 008 -- runtime settings for Workflow 6 (2026-09-15).
--
-- Values the workflows need at runtime that belong to the operator, not to the
-- code, and so must not be baked into the committed workflow JSON. One key
-- today: the operator's notification address (Section 9). Mailbox Watch reads
-- it for the reply notification's To, and to keep anything sent only to that
-- address -- the notifications themselves -- out of the Section 5 warm-up count.
--
-- Why a table and not an n8n environment variable: n8n 2.x blocks $env in Code
-- nodes and expressions unless N8N_BLOCK_ENV_ACCESS_IN_NODE=false, and turning
-- that off hands every Code node the whole container environment, including
-- N8N_ENCRYPTION_KEY and the database password. Operator decision, 2026-09-15:
-- keep n8n's default, and keep the address beside the contact data these
-- workflows already read.
--
-- Written only by n8n/sendtrack/sync_settings.py, from .env. Additive only.

BEGIN;

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY CHECK (key IN ('operator_email')),
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- One bare, lower-cased address: the shape the Sent mirror compares
  -- recipients in, and the build's rule for an address.
  CONSTRAINT settings_operator_email_shape CHECK (
    key <> 'operator_email'
    OR (value = lower(value) AND value ~ '^[^[:space:]@<>(),;:"'']+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$'))
);

COMMENT ON TABLE settings IS
  'Runtime settings for Workflow 6, written from .env by n8n/sendtrack/sync_settings.py. '
  'operator_email: where Mailbox Watch sends reply notifications; a message sent only to it '
  'does not count toward the warm-up ceiling. Never baked into workflow JSON (migration 008).';

COMMIT;
