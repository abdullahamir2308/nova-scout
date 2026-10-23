"""Proves every rule of migration 022 fails a test when it is broken.

    python n8n/sendtrack/test_hard_bounce_mutations.py

Operator decision 2026-10-24 (Section 9, Workflow 6): only a permanent bounce
counts toward the Send safety brake, and a permanent bounce marks the contact it
bounced from unverified. Each mutation breaks one rule in the shipped Record
Inbound statement or in migration 022 (the table's CHECKs, bounce_names_address()
and the unverify trigger); test_hard_bounce_sql.py must then fail, on one
scratch database. A mutation that makes a statement error counts as caught: the
workflow would fail on that message, and the test says so.

The brake's counting rule and the classifier's permanent/transient rules are
mutated by test_send_brake_mutations.py. Exits non-zero if any mutation
survives, or if the suite fails unmutated.
"""
import sys

import test_hard_bounce_sql as H

# (label, where, old, new) -- where is 'migration' or a statement key of H.shipped().
MUTATIONS = [
    # Record Inbound
    ("Record Inbound stores the kind on the bounce row", "record",
     "              THEN p.p->>'bounce_kind' END", "              THEN NULL END"),
    ("only a bounce matched to a lead carries a kind", "record",
     "CASE WHEN (SELECT lead_id FROM hit) IS NOT NULL AND p.p->>'classification' = 'bounce'",
     "CASE WHEN p.p->>'classification' = 'bounce'"),
    ("Record Inbound writes the kind onto the send", "record",
     "                            ELSE i.bounce_kind END", "                            ELSE NULL END"),
    ("a permanent bounce sticks on the send", "record",
     "                            WHEN o.bounce_kind = 'permanent' THEN 'permanent'\n", ""),
    ("the notification is told 'unverified' only when the bounce names the address", "record",
     "                        AND bounce_names_address(i.thread_ids, p.p->>'body_excerpt', c.email)\n", ""),
    ("the notification is told 'unverified' only for a permanent bounce", "record",
     "(SELECT i.bounce_kind = 'permanent' AND c.verified", "(SELECT i.bounce_kind IS NOT NULL AND c.verified"),
    # migration 022: the table
    ("the table refuses a kind on anything but a bounce", "migration",
     "  CHECK (bounce_kind IS NULL OR (bounce_kind IN ('permanent', 'transient') AND classification = 'bounce'));",
     "  CHECK (bounce_kind IS NULL OR bounce_kind IN ('permanent', 'transient'));"),
    ("only a permanent bounce unverifies a contact", "migration",
     "  WHEN (NEW.bounce_kind = 'permanent' AND NEW.lead_id IS NOT NULL)",
     "  WHEN (NEW.bounce_kind IS NOT NULL AND NEW.lead_id IS NOT NULL)"),
    ("only the contact whose address the bounce names", "migration",
     "     AND c.verified\n     AND bounce_names_address(NEW.thread_ids, NEW.body_excerpt, c.email);",
     "     AND c.verified;"),
    ("the backfill's UPDATE fires the rule too", "migration",
     "  AFTER INSERT OR UPDATE OF bounce_kind ON inbound_messages", "  AFTER INSERT ON inbound_messages"),
    ("the Sent mirror's recipients name the address (a report that never writes it)", "migration",
     "       EXISTS (SELECT 1\n                 FROM mailbox_sent ms, unnest(ms.recipients) r\n"
     "                WHERE ms.message_id = ANY (coalesce(thread_ids, '{}'::text[]))\n"
     "                  AND lower(r) = lower(btrim(address)))\n",
     "       false\n"),
    ("the report's own text names the address (the four bounces with no thread ids)", "migration",
     "    OR lower(coalesce(bounce_text, '')) ~ (", "    OR false AND lower(coalesce(bounce_text, '')) ~ ("),
    ("an address is never the tail of a longer one (xinfo@)", "migration",
     "    OR lower(coalesce(bounce_text, '')) ~ ('(^|[^a-z0-9._%+-])'", "    OR lower(coalesce(bounce_text, '')) ~ (''"),
    ("an address is never the head of a longer one (.example.au)", "migration",
     "         || '($|[^a-z0-9._%+-]|\\.($|[^a-z0-9-]))'))", "         || ''))"),
    ("an address is matched literally, not as a regex (a+b@)", "migration",
     r"regexp_replace(lower(btrim(address)), '([.+*?^$(){}|\[\]\\-])', '\\\1', 'g')", "lower(btrim(address))"),
    ("matching is case-insensitive (Contact@CRO6 vs contact@cro6)", "migration",
     "                  AND lower(r) = lower(btrim(address)))\n    -- The address",
     "                  AND r = btrim(address))\n    -- The address"),
    ("an empty address names nothing", "migration",
     "  SELECT coalesce(btrim(address), '') <> '' AND (", "  SELECT ("),
]


def variants():
    base = H.shipped()
    mig = H.migration()
    out = []
    for label, where, old, new in MUTATIONS:
        text = mig if where == "migration" else base[where]
        n = text.count(old)
        if n != 1:
            raise SystemExit("mutation %r: its text occurs %d times in %s, expected once" % (label, n, where))
        if where == "migration":
            out.append((label, dict(base), text.replace(old, new)))
        else:
            out.append((label, dict(base, **{where: text.replace(old, new)}), mig))
    return base, mig, out


if __name__ == "__main__":
    base, mig, muts = variants()
    H.create_scratch(mig)
    survived = 0
    try:
        failed = [r for r in H.checks(base) if not r[1]]
        if failed:
            print("baseline: test_hard_bounce_sql.py does not pass unmutated -- fix that first")
            for label, _, detail in failed:
                print("  FAIL", label, "--", detail[:300])
            sys.exit(1)
        print("baseline: test_hard_bounce_sql.py passes unmutated")
        for label, stmts, m in muts:
            try:
                H.psql(H.SCRATCH, m)
                caught = [r[0] for r in H.checks(stmts) if not r[1]]
                why = caught[0] if caught else None
            except SystemExit as e:
                why = "a statement errored: " + str(e).strip().splitlines()[-1][:120]
            except Exception as e:     # a check that cannot even run on the result is a failing check
                why = "the checks could not run: %s: %s" % (type(e).__name__, str(e)[:100])
            finally:
                H.psql(H.SCRATCH, mig)     # restore the shipped migration for the next mutation
            if why:
                print("  killed   %s\n           (%s)" % (label, why))
            else:
                survived += 1
                print("  SURVIVED %s" % label)
    finally:
        H.psql("postgres", "DROP DATABASE IF EXISTS %s WITH (FORCE);" % H.SCRATCH, check=False)
    print("\n%d mutations, %d killed, %d survived" % (len(MUTATIONS), len(MUTATIONS) - survived, survived))
    sys.exit(1 if survived else 0)
