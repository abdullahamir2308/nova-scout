"""Re-render every SENT CRO template email with the shipped CRO code and compare byte for byte.

    python n8n/drafting/rerender_sent_cro.py [--db novascout]

The proof the operator asked for on 2026-10-28 ("the CRO pipeline must not
change"): the Build First Touch and Build Follow-Up nodes in the CURRENT
n8n/workflows/drafting.json and follow-ups.json are run on the inputs each sent
email was written from, and their output is compared with what went out
(outreach_log.message_body, drafts.subject).

Inputs, read-only from the database:
  * first touch: the lead (company name, domain), its contact (name, address),
    its enrichment (areas, phases, city) and the recruiting-trial count, which is
    read back from the SENT subject ("{n} recruiting trial(s)") because
    ClinicalTrials.gov has moved on since -- the one input not stored anywhere.
  * follow-up: the same, plus the first email as sent (subject, body, date).
An email whose inputs have changed since it was written (a contact the operator
replaced with CONTACT, a bounce) cannot be expected to re-render identically;
those are listed with the reason, never silently counted as matches.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SQL = r"""
SELECT coalesce(json_agg(x ORDER BY x.draft_id), '[]') FROM (
  SELECT d.id AS draft_id, d.lead_id, d.variant, d.subject, o.message_body AS sent_body, o.sent_at,
         l.company_name, l.domain, c.name AS contact_name, c.email,
         e.therapeutic_areas, e.phases, e.raw_extraction->>'city' AS city,
         lead_company_type(e.raw_extraction, l.source) AS company_type,
         ft.subject AS first_subject, ft.message_body AS first_body,
         to_char(ft.sent_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS first_sent_at
    FROM outreach_log o
    JOIN drafts d ON d.id = o.draft_id
    JOIN leads l ON l.id = o.lead_id
    LEFT JOIN contacts c ON c.lead_id = l.id
    LEFT JOIN enrichments e ON e.lead_id = l.id
    LEFT JOIN LATERAL (
      SELECT d2.subject, o2.message_body, o2.sent_at
        FROM outreach_log o2 JOIN drafts d2 ON d2.id = o2.draft_id
       WHERE o2.lead_id = l.id AND o2.message_id IS NOT NULL AND coalesce(d2.variant, '') NOT LIKE 'follow-up-%'
       ORDER BY o2.sent_at LIMIT 1) ft ON true
   WHERE o.channel = 'email' AND o.message_id IS NOT NULL
     AND d.variant ~ '^(template/|follow-up-[0-9]+/template/)'
) x;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="novascout")
    a = ap.parse_args()
    res = subprocess.run(["docker", "exec", "-i", "nova-scout-postgres-1", "psql", "-U", "novascout", "-d", a.db, "-At",
                          "-v", "ON_ERROR_STOP=1", "-c", "SET default_transaction_read_only = on;", "-c", SQL],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        sys.exit(res.stderr)
    out = res.stdout
    rows = json.loads(out[out.index("["):])
    node = subprocess.run(["node", os.path.join(HERE, "rerender_sent_cro.js")], input=json.dumps(rows),
                          capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(node.stdout)
    sys.stderr.write(node.stderr)
    sys.exit(node.returncode)


if __name__ == "__main__":
    main()
