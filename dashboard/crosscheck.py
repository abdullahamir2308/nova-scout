"""Cross-check every dashboard number against a direct SQL query.

    python dashboard/crosscheck.py              both data paths (below)
    python dashboard/crosscheck.py grafana      the Grafana dashboard only
    python dashboard/crosscheck.py page         the custom page only

For each number, two independent answers:
  * the DASHBOARD's, by one of two paths:
      grafana  the panel's own SQL, taken from the built dashboard (grafana/dashboards/nova-scout.json), run
               through the running Grafana's query API (POST /api/ds/query) -- so through the provisioned data
               source, as the read-only role nova_dashboard, reading only the metrics views. The niche variable
               is filled in the way Grafana's ${niche:sqlstring} fills it.
      page     what the custom page is sent: GET /api/data?niche=... on the running `dashboard` service
               (127.0.0.1:3001, web/server.py), the same JSON the page draws from, which the service reads as
               nova_dashboard from the same views. The page only lays these numbers out; the one computed on
               the page (the free slots, cap - sent) is computed here the same way.
  * DIRECT: a query written separately against the base tables, run as the superuser with psql. It uses the
    system's own definitions where one exists (lead_niche(), send_brake_stats(), hold_reason_class(),
    hold_needs_person(), lead_held_source()), and works the warm-up cap out in Python from Section 5's table.
    The page path reuses these same direct queries (below, d_*): no SQL of its own.

Run for "All" and for each niche on its own. Every cell must agree (rounded values to 0.05). A pair that
disagrees is re-run once, because a send or an inbound message landing between the two reads is real
movement, not a defect; a second disagreement fails. Read-only on both sides. Exit 0 only if all pass.

Reads NOVASCOUT_GRAFANA_ADMIN_PASSWORD from .env for the Grafana API login (127.0.0.1:3000); the page needs none.
"""
import base64
import csv
import datetime
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "web"))
import build_dashboard as B  # noqa: E402
import server as S  # noqa: E402  (web/server.py: standard library only; importing it starts nothing)

ENV_FILE = os.environ.get("NOVASCOUT_ENV_FILE", os.path.join(ROOT, ".env"))
GRAFANA = os.environ.get("NOVASCOUT_GRAFANA_URL", "http://127.0.0.1:3000")
PAGE = os.environ.get("NOVASCOUT_PAGE_URL", "http://127.0.0.1:%d" % S.PORT)
PG = "nova-scout-postgres-1"
DB = "novascout"
ZONE = "Asia/Karachi"
KARACHI = datetime.timezone(datetime.timedelta(hours=5))  # no DST there; the ceiling's own clock

# lead -> niche, the system's definition, for the direct queries
LN = ("ln AS (SELECT l.id AS lead_id, coalesce(lead_niche(e.raw_extraction, l.source), 'none') AS niche "
      "FROM leads l LEFT JOIN enrichments e ON e.lead_id = l.id)")


def load_env(key):
    with io.open(ENV_FILE, encoding="utf-8-sig") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln.startswith(key + "="):
                v = ln.split("=", 1)[1].strip()
                return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v
    return ""


AUTH = "Basic " + base64.b64encode(("admin:" + load_env("NOVASCOUT_GRAFANA_ADMIN_PASSWORD")).encode()).decode()


def grafana(sql):
    body = {"queries": [{"refId": "A", "datasource": B.DS, "rawSql": sql, "format": "table"}],
            "from": "now-14d", "to": "now"}
    req = urllib.request.Request(GRAFANA + "/api/ds/query", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": AUTH})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Grafana %s: %s" % (e.code, e.read().decode()[:400]))
    r = res["results"]["A"]
    if r.get("error"):
        raise RuntimeError("Grafana query error: %s" % r["error"])
    frame = r["frames"][0]
    names = [f["name"] for f in frame["schema"]["fields"]]
    cols = frame["data"]["values"] if frame.get("data") else [[] for _ in names]
    rows = [list(t) for t in zip(*cols)] if cols and cols[0] is not None else []
    return names, rows


def psql(sql):
    r = subprocess.run(["docker", "exec", "-i", PG, "psql", "-U", "novascout", "-d", DB, "-v", "ON_ERROR_STOP=1",
                        "-q", "--csv", "-f", "-"],
                       input="BEGIN READ ONLY;\n" + sql + "\nROLLBACK;\n", capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError("psql: %s" % r.stderr.strip())
    rows = list(csv.reader(r.stdout.splitlines()))
    return rows[0], [[None if v == "" else v for v in r] for r in rows[1:]]


def num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def same(a, b):
    a, b = num(a), num(b)
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 0.05
    return a == b


def niche_sets():
    _, rows = psql("SELECT code FROM niches UNION ALL SELECT 'none' ORDER BY 1;")
    every = [r[0] for r in rows]
    return [("All", every)] + [(n, [n]) for n in every]


def panel_sql(dash, title, ref="A"):
    for p in dash["panels"]:
        if p.get("title") == title:
            for t in p["targets"]:
                if t["refId"] == ref:
                    return t["rawSql"]
    raise KeyError(title)


def fill(sql, niches):
    return sql.replace("${niche:sqlstring}", ",".join("'%s'" % n for n in niches))


def arr(niches):
    return "ARRAY[%s]::text[]" % ",".join("'%s'" % n for n in niches)


def karachi_day(ms_or_text):
    if isinstance(ms_or_text, (int, float)):
        return datetime.datetime.fromtimestamp(ms_or_text / 1000.0, KARACHI).date()
    return datetime.date.fromisoformat(str(ms_or_text)[:10])


def warmup_cap(first_day, day):
    table = B.load_warmup(B.read(B.MASTER_REF))
    idx = max(0, (day - first_day).days) if first_day else 0
    week = idx // 7 + 1
    return max(n for w, n in table if w <= week)


# ---------------------------------------------------------------------------------------------------------
# One direct computation per panel: returns (columns, rows) shaped like the panel's
# ---------------------------------------------------------------------------------------------------------
COUNTED = ("counted AS (SELECT m.sent_at AS at, m.lead_id FROM mailbox_sent m WHERE m.external "
           "UNION ALL SELECT o.sent_at, o.lead_id FROM outreach_log o WHERE o.channel = 'email' AND o.message_id IS NULL)")


def d_sent_today(niches):
    _, r = psql("WITH %s, %s SELECT count(*) FILTER (WHERE (k.at AT TIME ZONE '%s')::date = (now() AT TIME ZONE '%s')::date "
                "AND coalesce(ln.niche, 'none') = ANY (%s)), (min(k.at) AT TIME ZONE '%s')::date, "
                "(now() AT TIME ZONE '%s')::date FROM counted k LEFT JOIN ln ON ln.lead_id = k.lead_id;"
                % (LN, COUNTED, ZONE, ZONE, arr(niches), ZONE, ZONE))
    sent, first, today = int(r[0][0]), r[0][1], datetime.date.fromisoformat(r[0][2])
    cap = warmup_cap(datetime.date.fromisoformat(first) if first else None, today)
    return [[sent, cap, cap - sent]]


def d_per_day(niches):
    _, r = psql("WITH %s, %s SELECT (k.at AT TIME ZONE '%s')::date AS day, coalesce(ln.niche, 'none') AS niche, count(*) "
                "FROM counted k LEFT JOIN ln ON ln.lead_id = k.lead_id "
                "WHERE (k.at AT TIME ZONE '%s')::date > (now() AT TIME ZONE '%s')::date - 14 "
                "AND coalesce(ln.niche, 'none') = ANY (%s) GROUP BY 1, 2;" % (LN, COUNTED, ZONE, ZONE, ZONE, arr(niches)))
    _, t = psql("SELECT (now() AT TIME ZONE '%s')::date;" % ZONE)
    today = datetime.date.fromisoformat(t[0][0])
    got = {(datetime.date.fromisoformat(d), n): int(c) for d, n, c in r}
    return sorted([[(today - datetime.timedelta(days=i)).isoformat(), n,
                    got.get((today - datetime.timedelta(days=i), n), 0)] for i in range(14) for n in niches])


def d_cap_per_day(_niches):
    _, r = psql("WITH %s SELECT (min(at) AT TIME ZONE '%s')::date, (now() AT TIME ZONE '%s')::date FROM counted;"
                % (COUNTED, ZONE, ZONE))
    first = datetime.date.fromisoformat(r[0][0]) if r[0][0] else None
    today = datetime.date.fromisoformat(r[0][1])
    days = [today - datetime.timedelta(days=i) for i in range(13, -1, -1)]
    return [[d.isoformat(), warmup_cap(first, d)] for d in days]


def d_runway(niches):
    _, r = psql("WITH %s SELECT "
                "(SELECT count(*) FROM drafts d JOIN ln ON ln.lead_id = d.lead_id WHERE d.channel = 'email' "
                " AND d.status = 'approved' AND coalesce(d.variant, '') NOT LIKE 'reply/%%' "
                " AND lead_held_source(d.lead_id) IS NULL AND ln.niche = ANY (%s)), "
                "(SELECT count(*) FROM outreach_log o JOIN ln ON ln.lead_id = o.lead_id LEFT JOIN drafts d ON d.id = o.draft_id "
                " WHERE o.channel = 'email' AND o.message_id IS NOT NULL AND coalesce(d.variant, '') NOT LIKE 'reply/%%' "
                " AND o.sent_at > now() - interval '7 days' AND ln.niche = ANY (%s));" % (LN, arr(niches), arr(niches)))
    approved, sends = int(r[0][0]), int(r[0][1])
    per_day = round(sends / 7.0, 1)
    return [[approved, per_day, round(approved / (sends / 7.0), 1) if sends else None]]


def d_bounce(_niches):
    _, r = psql("SELECT s->>'sends', s->>'bounced' FROM (SELECT send_brake_stats(now(), 3) AS s) x;")
    sends, bounced = int(r[0][0]), int(r[0][1])
    _, pct = B.load_brake(B.read(B.MASTER_REF))
    return [[round(100.0 * bounced / sends, 1) if sends else 0, bounced, sends, pct]]


def d_brake(_niches):
    _, r = psql("SELECT count(*) FROM send_pauses WHERE cleared_at IS NULL;")
    return [["Sending: no pause"]] if r[0][0] == "0" else [["PAUSED"]]


def d_spam(niches):
    _, r = psql("WITH %s SELECT (SELECT jsonb_array_length(send_brake_stats(now(), 3)->'complaints')), "
                "(SELECT count(*) FROM inbound_messages i LEFT JOIN ln ON ln.lead_id = i.lead_id "
                " WHERE i.spam_complaint AND coalesce(ln.niche, 'none') = ANY (%s));" % (LN, arr(niches)))
    return [[int(r[0][0]), int(r[0][1])]]


def d_replies(niches):
    _, r = psql("WITH %s SELECT count(*), count(*) FILTER (WHERE i.reply_outcome IN ('positive', 'meeting booked')), "
                "count(*) FILTER (WHERE i.reply_outcome = 'meeting booked'), count(*) FILTER (WHERE i.reply_outcome IS NULL) "
                "FROM inbound_messages i LEFT JOIN ln ON ln.lead_id = i.lead_id "
                "WHERE i.classification = 'reply' AND coalesce(ln.niche, 'none') = ANY (%s);" % (LN, arr(niches)))
    return [[int(v) for v in r[0]]]


def d_by_type(niches):
    _, r = psql("WITH %s SELECT i.classification, "
                "count(*) FILTER (WHERE coalesce(i.received_at, i.processed_at) >= now() - interval '7 days'), count(*), "
                "count(*) FILTER (WHERE i.bounce_kind = 'permanent'), count(*) FILTER (WHERE i.bounce_kind = 'transient') "
                "FROM inbound_messages i LEFT JOIN ln ON ln.lead_id = i.lead_id "
                "WHERE coalesce(ln.niche, 'none') = ANY (%s) GROUP BY 1;" % (LN, arr(niches)))
    got = {c: [int(x) for x in rest] for c, *rest in r}
    order = [("reply", "reply"), ("opt-out", "opt-out"), ("out-of-office", "auto-reply"), ("bounce", "bounce"),
             ("unmatched", "unmatched")]
    return [[label] + got.get(cls, [0, 0, 0, 0]) for label, cls in order]


def d_latest(niches):
    _, r = psql("WITH %s SELECT extract(epoch FROM coalesce(i.received_at, i.processed_at)) * 1000, l.company_name, "
                "i.classification, coalesce(i.reply_outcome::text, 'not tagged') FROM inbound_messages i JOIN leads l ON l.id = i.lead_id "
                "LEFT JOIN ln ON ln.lead_id = i.lead_id WHERE i.classification IN ('reply', 'opt-out') "
                "AND coalesce(ln.niche, 'none') = ANY (%s) ORDER BY 1 DESC LIMIT 20;" % (LN, arr(niches)))
    return [[round(float(t)), c, k, o] for t, c, k, o in r]


def d_ab(niches):
    # the Daily Digest's niche_totals query (n8n/sendtrack/build_workflow.py, Load Digest), plus positive tags
    _, r = psql("SELECT coalesce(lead_niche(e.raw_extraction, l.source), 'none') AS niche, "
                "substring(d.variant from '(?:^|/)template/([AB])') AS arm, "
                "count(*) FILTER (WHERE d.variant NOT LIKE 'follow-up-%')::int AS first_touches, "
                "count(*) FILTER (WHERE d.variant LIKE 'follow-up-%')::int AS follow_ups, "
                "count(DISTINCT o.lead_id) FILTER (WHERE o.replied)::int AS replied_leads, "
                "count(DISTINCT o.lead_id) FILTER (WHERE EXISTS (SELECT 1 FROM inbound_messages i WHERE i.lead_id = o.lead_id "
                "  AND i.classification = 'reply' AND i.reply_outcome IN ('positive', 'meeting booked')))::int AS positive "
                "FROM outreach_log o JOIN drafts d ON d.id = o.draft_id JOIN leads l ON l.id = o.lead_id "
                "LEFT JOIN enrichments e ON e.lead_id = l.id WHERE o.channel = 'email' AND o.message_id IS NOT NULL "
                "AND d.variant ~ '(^|/)template/[AB]' GROUP BY 1, 2;")
    sample = B.load_ab_sample(B.read(B.MASTER_REF))
    got = {(n, a): [int(x) for x in rest] for n, a, *rest in r}
    out = []
    for n in sorted(niches):
        for a in ("A", "B"):
            ft, fu, rep, pos = got.get((n, a), [0, 0, 0, 0])
            if n == "none" and ft == 0:
                continue
            both = min(got.get((n, x), [0])[0] for x in ("A", "B"))
            status = ("sample reached in both arms" if both >= sample
                      else "collecting: no winner before %d first touches in each arm" % sample)
            out.append([n, a, "%d of %d needed" % (ft, sample), fu, rep, pos,
                        round(100.0 * rep / ft, 1) if ft else None, status])
    return out


def d_funnel(niches):
    _, r = psql("WITH %s SELECT ln.niche, l.source, count(*), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM enrichments e WHERE e.lead_id = l.id)), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM scores s WHERE s.lead_id = l.id)), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM scores s WHERE s.lead_id = l.id AND s.disqualified)), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM contacts c WHERE c.lead_id = l.id AND btrim(c.email) <> '')), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.channel = 'email' "
                "  AND coalesce(d.variant, '') NOT LIKE 'reply/%%')), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.channel = 'email' "
                "  AND coalesce(d.variant, '') NOT LIKE 'reply/%%' AND (d.status IN ('approved', 'sent') OR d.approved_at IS NOT NULL))), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND o.channel = 'email' "
                "  AND o.message_id IS NOT NULL)), "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND o.channel = 'email' "
                "  AND o.message_id IS NOT NULL AND o.replied)) "
                "FROM leads l JOIN ln ON ln.lead_id = l.id WHERE ln.niche = ANY (%s) GROUP BY 1, 2 ORDER BY 1, 2;"
                % (LN, arr(niches)))
    return [[n, s] + [int(x) for x in rest] for n, s, *rest in r]


def d_held(niches):
    # the Daily Digest's HELD classes and its NEEDS YOU rule (Load Digest)
    _, r = psql("WITH %s SELECT hold_reason_class(d.hold_reason), count(*), count(*) FILTER (WHERE d.channel = 'email' "
                "AND coalesce(d.variant, '') NOT LIKE 'low-context%%' AND coalesce(d.variant, '') NOT LIKE 'no-send-clock%%' "
                "AND hold_needs_person(d.hold_reason) AND lead_held_source(d.lead_id) IS NULL) "
                "FROM drafts d LEFT JOIN ln ON ln.lead_id = d.lead_id WHERE d.status = 'pending' "
                "AND coalesce(ln.niche, 'none') = ANY (%s) GROUP BY 1 ORDER BY 2 DESC, 1;" % (LN, arr(niches)))
    return [[c, int(n), int(p)] for c, n, p in r]


def d_niches(_niches):
    _, r = psql("SELECT code FROM niches UNION ALL SELECT 'none' ORDER BY 1;")
    return [[x[0]] for x in r]


# (label, panel title, refId, direct, how to turn the panel's rows into the direct shape)
def as_days(rows):
    return sorted([[karachi_day(r[0]).isoformat()] + r[1:] for r in rows])


def brake_text(rows):
    return [["PAUSED" if str(rows[0][0]).startswith("PAUSED") else rows[0][0]]]


def ms_round(rows):
    # received, company, type, outcome (the panel also shows country, niche, arm, subject, excerpt)
    return [[round(float(r[0])), r[1], r[5], r[6]] for r in rows]


def drop_tail(n):
    return lambda rows: [r[:n] for r in rows]


CHECKS = [
    ("niche filter values", None, "A", d_niches, None),
    ("Sent today / daily cap", "Sent today / daily cap", "A", d_sent_today, None),
    ("Sends per day (14 days, by niche)", "Sends per day, last 14 days", "A", d_per_day, as_days),
    ("Daily cap per day (14 days)", "Sends per day, last 14 days", "B", d_cap_per_day, as_days),
    ("Approved queue left", "Approved queue left", "A", d_runway, None),
    ("Permanent-bounce rate (the brake's)", "Permanent-bounce rate, last 3 days", "A", d_bounce, None),
    ("Send brake", "Send brake", "A", d_brake, brake_text),
    ("Spam complaints", "Spam complaints", "A", d_spam, None),
    ("Replies and positive replies", "Replies and positive replies", "A", d_replies, None),
    ("What came back, by type", "What came back, by type", "A", d_by_type, None),
    ("Latest replies", "Latest replies", "A", d_latest, ms_round),
    ("A/B arms", "Arm A against arm B", "A", d_ab, None),
    ("Funnel", "Funnel by niche and source", "A", d_funnel, None),
    ("Held drafts by reason", "Held drafts by reason", "A", d_held, None),
]
PER_NICHE = {"Sent today / daily cap", "Sends per day (14 days, by niche)", "Approved queue left", "Spam complaints",
             "Replies and positive replies", "What came back, by type", "Latest replies", "A/B arms", "Funnel",
             "Held drafts by reason"}


def run_one(dash, label, title, ref, direct, shape, niches):
    if title is None:
        sql = dash["templating"]["list"][0]["query"]
    else:
        sql = fill(panel_sql(dash, title, ref), niches)
    _, prow = grafana(sql)
    if shape:
        prow = shape(prow)
    drow = direct(niches)
    ok = len(prow) == len(drow) and all(len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
                                        for a, b in zip(prow, drow))
    return ok, prow, drow


def report(label, set_name, ok, prow, drow):
    summary = json.dumps(prow if len(prow) <= 3 else prow[:2] + [["... %d rows" % len(prow)]], default=str)
    if ok:
        print("PASS  %-36s %-14s %s" % (label, set_name, summary[:150]))
    else:
        print("FAIL  %-36s %-14s\n      dashboard: %s\n      direct   : %s" % (label, set_name, prow, drow))
    return ok


def run_grafana():
    dash = json.loads(B.read(B.OUT))
    fresh, _ = B.build()
    if B.render(fresh) != B.read(B.OUT):
        sys.exit("grafana/dashboards/nova-scout.json is stale -- run dashboard/build_dashboard.py first")
    passed = failed = 0
    for label, title, ref, direct, shape in CHECKS:
        sets = niche_sets() if label in PER_NICHE else [("All", niche_sets()[0][1])]
        for set_name, niches in sets:
            ok, prow, drow = run_one(dash, label, title, ref, direct, shape, niches)
            if not ok:
                ok, prow, drow = run_one(dash, label, title, ref, direct, shape, niches)
            if report(label, set_name, ok, prow, drow):
                passed += 1
            else:
                failed += 1
    return passed, failed


# ---------------------------------------------------------------------------------------------------------
# The custom page's path: GET /api/data on the `dashboard` service, against the same direct queries
# ---------------------------------------------------------------------------------------------------------
def page_get(niches, every):
    q = "all" if niches == every else ",".join(niches)
    try:
        with urllib.request.urlopen(PAGE + "/api/data?niche=" + q, timeout=60) as res:
            return json.load(res)
    except urllib.error.URLError as e:
        raise RuntimeError("the dashboard page service did not answer at %s: %s (docker compose up -d dashboard)"
                           % (PAGE, e))


# What the page is sent, shaped like the direct answer. Where the page shows fewer columns than the direct
# query computes, or sums rows the direct query keeps apart, the DIRECT side is reshaped (never the page's).
def p_niches(a):
    return sorted([o["key"]] for o in a["options"])


def p_sent_today(a):
    return [[a["today"]["sent"], a["today"]["cap"], a["today"]["cap"] - a["today"]["sent"]]]


def p_per_day(a):
    return sorted([x["iso"], n, x["n"][n]] for x in a["days"] for n in a["selected"])


def p_runway(a):
    q = a["queue"]
    return [[q["approved"], q["perDay"], q["daysLeft"]]]


def p_bounce(a):
    b = a["bounce"]
    return [[b["pct"], b["bounces"], b["sends"], b["brakeAt"]]]


def p_brake_days(a):
    return [[a["bounce"]["windowDays"]]]


def d_brake_days(_niches):
    return [[B.load_brake(B.read(B.MASTER_REF))[0]]]


def p_brake(a):
    return [["PAUSED" if a["brake"]["paused"] else "Sending: no pause"]]


def p_spam(a):
    return [[a["spam"]["counted"], a["spam"]["ever"]]]


def p_replies(a):
    r = a["replies"]
    return [[r["total"], r["positive"], r["meetings"], r["untagged"]]]


def p_by_type(a):
    return [[c["type"], c["last7"], c["total"], c["permanent"], c["delay"]] for c in a["came"]]


def p_latest(a):
    return [[round(r["ts"] * 1000), r["company_name"], r["type"], r["outcome"] or "not tagged"] for r in a["latest"]]


def p_ab(a):
    return sorted([n["niche"], x["a"], x["n"], x["fu"], x["rep"], n["reached"]] for n in a["ab"] for x in n["arms"])


def ab_core(rows):
    # d_ab: niche, arm, "n of N needed", follow-ups, replies, positive, rate, status
    return sorted([r[0], r[1], int(r[2].split(" of ")[0]), r[3], r[4], r[7] == "sample reached in both arms"] for r in rows)


def p_need(a):
    return [[a["need"]]]


def d_need(_niches):
    return [[B.load_ab_sample(B.read(B.MASTER_REF))]]


def p_funnel(a):
    f = a["funnel"]
    return [[f["leads"], f["enriched"], f["scored"], f["contact"], f["drafted"], f["approved"], f["sent"],
             f["disqualified"]]]


def funnel_totals(rows):
    # d_funnel: niche, source, leads, enriched, scored, disqualified, contact, drafted, approved, sent, replied
    col = lambda i: sum(r[i] for r in rows)  # noqa: E731
    return [[col(2), col(3), col(4), col(6), col(7), col(8), col(9), col(5)]]


def p_held(a):
    return [[h["cls"], h["held"], h["needs"]] for h in a["held"]]


# (label, direct, the page's answer shaped like the direct one, a reshaping of the direct answer or None)
PAGE_CHECKS = [
    ("niche buttons (the niche list)", d_niches, p_niches, None),
    ("Sent today / daily cap", d_sent_today, p_sent_today, None),
    ("Sent per day (14 days, by niche)", d_per_day, p_per_day, None),
    ("Approved queue / sends a day", d_runway, p_runway, None),
    ("Bounce rate (the brake's)", d_bounce, p_bounce, None),
    ("Bounce window (days, from the doc)", d_brake_days, p_brake_days, None),
    ("Send brake", d_brake, p_brake, None),
    ("Spam complaints", d_spam, p_spam, None),
    ("Replies tiles", d_replies, p_replies, None),
    ("What came back", d_by_type, p_by_type, None),
    ("Latest replies", d_latest, p_latest, lambda rows: rows[:S.LATEST_N]),
    ("A/B arms (counts, sample reached)", d_ab, p_ab, ab_core),
    ("A/B sample per arm (from the doc)", d_need, p_need, None),
    ("Funnel (sums over the selection)", d_funnel, p_funnel, funnel_totals),
    ("Held drafts by reason", d_held, p_held, None),
]
PAGE_PER_NICHE = {"Sent today / daily cap", "Sent per day (14 days, by niche)", "Approved queue / sends a day",
                  "Spam complaints", "Replies tiles", "What came back", "Latest replies",
                  "A/B arms (counts, sample reached)", "Funnel (sums over the selection)", "Held drafts by reason"}


def run_page_one(direct, shape, adapt, niches, every):
    prow = shape(page_get(niches, every))
    drow = direct(niches)
    if adapt:
        drow = adapt(drow)
    ok = len(prow) == len(drow) and all(len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
                                        for a, b in zip(prow, drow))
    return ok, prow, drow


def run_page():
    passed = failed = 0
    # the service serves the committed page, byte for byte
    with urllib.request.urlopen(PAGE + "/", timeout=30) as res:
        served = res.read()
    with io.open(os.path.join(HERE, "web", "index.html"), "rb") as fh:
        committed = fh.read()
    ok = served == committed
    print("%s  %-36s %-14s %d bytes" % ("PASS" if ok else "FAIL", "served page is web/index.html", "-", len(served)))
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    every = niche_sets()[0][1]
    for label, direct, shape, adapt in PAGE_CHECKS:
        sets = niche_sets() if label in PAGE_PER_NICHE else [("All", every)]
        for set_name, niches in sets:
            ok, prow, drow = run_page_one(direct, shape, adapt, niches, every)
            if not ok:
                ok, prow, drow = run_page_one(direct, shape, adapt, niches, every)
            if report(label, set_name, ok, prow, drow):
                passed += 1
            else:
                failed += 1
    return passed, failed


def main():
    which = set(sys.argv[1:]) or {"grafana", "page"}
    if which - {"grafana", "page"}:
        sys.exit("usage: crosscheck.py [grafana] [page]")
    passed = failed = 0
    for name, run in (("grafana", run_grafana), ("page", run_page)):
        if name in which:
            print("\n== %s path ==" % name)
            p, f = run()
            print("%s path: %d checks passed, %d failed" % (name, p, f))
            passed, failed = passed + p, failed + f
    print("\n%d checks passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
