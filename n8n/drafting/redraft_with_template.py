"""Retire the unsent AI-composed first touches and redraft their leads with the fixed template.

    python n8n/drafting/redraft_with_template.py                 # show what would change, change nothing
    python n8n/drafting/redraft_with_template.py --retire-only   # step 1: retire the drafts, leave the leads
    python n8n/drafting/redraft_with_template.py --apply         # step 2: retire any new ones, re-queue the leads

One-off, for the switch to template outreach (operator brief 2026-10-19,
Master Ref Section 9 Workflow 4). Two steps, because of how n8n publishes:

  1. --retire-only, run as soon as the switch was decided. Every approved or
     pending first-touch EMAIL draft that is not a template draft, belonging to
     a lead that has never been emailed, becomes 'rejected' / 'bad draft', with
     RETIRED_MARK appended to its hold_reason so step 2 can find it. Its lead
     stays at 'drafted' with no live first touch: nothing can send to it, and
     the Drafting workflow still running from n8n's memory cannot pick it up.

  2. --apply, run only AFTER the template Drafting workflow is published. It
     refuses otherwise: an import leaves the old, model-composing Drafting
     running from memory until the UI publishes the new one (Section 3), and a
     lead put back to 'contact_found' before that would be composed by Sonnet,
     auto-approved and sent. In one transaction it retires any non-template
     first touch written since step 1, then puts every lead that has a retired
     draft, sits at 'drafted', is a CRO, has never been emailed and has no live
     first-touch email back to 'contact_found'. The next Drafting tick writes
     its template draft.

Never touched: sent drafts, follow-ups, replies, LinkedIn drafts, and every lead
that has ever been emailed (outreach_log or the Sent-folder mirror).
"""
import json
import subprocess
import sys

PG = "nova-scout-postgres-1"
PGUSER = "novascout"
RETIRED_MARK = "retired 2026-10-19: replaced by the fixed template (Section 9, Workflow 4)"
# The node the template build carries and the composing build does not.
TEMPLATE_NODE = "Build First Touch"
COMPOSER_NODE = "Claude Draft"

# A first-touch email draft that is still live and is not a template draft.
LIVE_AI_FIRST_TOUCH = """
      d.channel = 'email'
  AND d.status IN ('pending', 'approved')
  AND coalesce(d.variant, '') NOT LIKE 'follow-up-%'
  AND coalesce(d.variant, '') NOT LIKE 'reply/%'
  AND coalesce(d.variant, '') NOT LIKE 'template/%'"""

NEVER_EMAILED = """
      NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = %(lead)s AND o.channel = 'email')
  AND NOT EXISTS (SELECT 1 FROM mailbox_sent m WHERE m.lead_id = %(lead)s)"""

RETIRE_SQL = """
UPDATE drafts d
   SET status = 'rejected',
       reject_reason = 'bad draft',
       hold_reason = concat_ws('; ', nullif(d.hold_reason, ''), '%(mark)s')
 WHERE %(live)s
   AND %(never)s
RETURNING d.id, d.lead_id, d.variant;""" % {
    "mark": RETIRED_MARK, "live": LIVE_AI_FIRST_TOUCH, "never": NEVER_EMAILED % {"lead": "d.lead_id"}}

REQUEUE_SQL = """
UPDATE leads l
   SET status = 'contact_found', updated_at = now()
  FROM enrichments e
 WHERE e.lead_id = l.id
   AND l.status = 'drafted'
   AND lead_company_type(e.raw_extraction, l.source) = 'CRO'
   AND EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.status = 'rejected'
                 AND position('%(mark)s' IN coalesce(d.hold_reason, '')) > 0)
   AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.channel = 'email'
                     AND d.status IN ('pending', 'approved', 'sent')
                     AND coalesce(d.variant, '') NOT LIKE 'reply/%%')
   AND %(never)s
RETURNING l.id;""" % {"mark": RETIRED_MARK, "never": NEVER_EMAILED % {"lead": "l.id"}}


def psql(db, sql):
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", PGUSER, "-d", db, "-X", "-q", "-At",
                        "-v", "ON_ERROR_STOP=1"], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit("psql failed (%d):\n%s" % (p.returncode, p.stderr))
    return p.stdout


def rows(db, sql):
    return json.loads(psql(db, "SELECT coalesce(json_agg(t), '[]'::json) FROM (%s) t;" % sql.strip().rstrip(";"))
                      .strip())


def template_drafting_published():
    """(ok, why). Published means the version n8n activated is the template build."""
    got = rows("n8n", """SELECT w.active, w."activeVersionId" AS version,
                                position('%s' IN h.nodes::text) > 0 AS template,
                                position('%s' IN h.nodes::text) > 0 AS composer
                           FROM workflow_entity w
                           LEFT JOIN workflow_history h ON h."versionId" = w."activeVersionId"
                          WHERE w.id = 'drafting0001'""" % (TEMPLATE_NODE, COMPOSER_NODE))
    if not got:
        return False, "workflow drafting0001 does not exist in n8n"
    w = got[0]
    if not w["active"] or not w["version"]:
        return False, "Drafting (drafting0001) is not published -- publish it in the n8n UI first"
    if w["composer"] or not w["template"]:
        return False, ("the published Drafting version %s is the model-composing build, not the template build "
                       "-- import the new drafting.json and publish it in the n8n UI first" % w["version"])
    return True, "published version %s is the template build" % w["version"]


def preview():
    retire = rows("novascout", """SELECT d.id, d.lead_id, d.status, d.variant FROM drafts d
                                   WHERE %s AND %s ORDER BY d.id""" % (LIVE_AI_FIRST_TOUCH,
                                                                       NEVER_EMAILED % {"lead": "d.lead_id"}))
    marked = rows("novascout", """SELECT DISTINCT d.lead_id, l.status FROM drafts d JOIN leads l ON l.id = d.lead_id
                                   WHERE d.status = 'rejected' AND position('%s' IN coalesce(d.hold_reason, '')) > 0
                                   ORDER BY 1""" % RETIRED_MARK)
    return retire, marked


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("", "--retire-only", "--apply"):
        raise SystemExit(__doc__)
    retire, marked = preview()
    print("live non-template first touches on never-emailed leads: %d" % len(retire))
    for r in retire:
        print("  draft %(id)s  lead %(lead_id)s  %(status)s  %(variant)s" % r)
    print("leads already carrying a retired draft: %d (%s)" % (
        len(marked), ", ".join("%s:%s" % (m["lead_id"], m["status"]) for m in marked)))
    if not mode:
        ok, why = template_drafting_published()
        print("re-queue allowed now: %s (%s)" % (ok, why))
        print("nothing changed (no flag given)")
        return
    if mode == "--apply":
        ok, why = template_drafting_published()
        if not ok:
            raise SystemExit("REFUSED: %s. Nothing was changed." % why)
        print(why)
        out = psql("novascout", "BEGIN;\n" + RETIRE_SQL + "\n" + REQUEUE_SQL + "\nCOMMIT;\n")
    else:
        out = psql("novascout", "BEGIN;\n" + RETIRE_SQL + "\nCOMMIT;\n")
    print(out.strip() or "(no rows changed)")


if __name__ == "__main__":
    main()
