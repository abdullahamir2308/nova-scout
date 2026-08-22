-- Nova Scout — novascout database schema
-- Source of truth: NovaScout_MasterRef.md, Section 8. Do not add columns
-- or enum-style enforcement here beyond what that section documents —
-- status-like fields stay plain TEXT because values are still evolving.

CREATE TABLE leads (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domain       TEXT NOT NULL UNIQUE,
    company_name TEXT,
    country      TEXT,
    source       TEXT,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN leads.status IS
    'Allowed values (Section 8): ingested -> enriched -> scored -> disqualified | contact_found -> drafted -> approved -> sent -> replied -> won | lost. Plain TEXT, not an ENUM — values may still evolve.';

CREATE TABLE enrichments (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id            BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    therapeutic_areas  TEXT[],
    phases             TEXT[],
    founder_name       TEXT,
    founder_linkedin   TEXT,
    employee_estimate  INTEGER,
    has_chatbot        BOOLEAN NOT NULL DEFAULT false,
    chatbot_vendor     TEXT,
    site_quality_notes TEXT,
    raw_extraction     JSONB,
    enriched_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE scores (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id           BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    fit_score         INTEGER CHECK (fit_score BETWEEN 0 AND 100),
    disqualified      BOOLEAN NOT NULL DEFAULT false,
    disqualify_reason TEXT,
    rationale         TEXT,
    scored_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contacts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id      BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    name         TEXT,
    title        TEXT,
    email        TEXT,
    linkedin_url TEXT,
    apollo_id    TEXT,
    verified     BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE drafts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id       BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel       TEXT,
    variant       TEXT,
    subject       TEXT,
    body          TEXT,
    status        TEXT,
    reject_reason TEXT,
    edited_body   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN drafts.channel IS 'Allowed values (Section 8): email | linkedin.';
COMMENT ON COLUMN drafts.status IS 'Allowed values (Section 8): pending | approved | rejected | sent.';

CREATE TABLE outreach_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id      BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel      TEXT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_body TEXT,
    replied      BOOLEAN NOT NULL DEFAULT false,
    replied_at   TIMESTAMPTZ,
    reply_body   TEXT,
    outcome      TEXT
);

CREATE TABLE blocklist (
    domain   TEXT PRIMARY KEY,
    reason   TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
