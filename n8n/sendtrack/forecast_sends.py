"""14-day send forecast, every niche: the SHIPPED Decide Send, ticked every 10 minutes.

    python n8n/sendtrack/forecast_sends.py [--days 14] [--runs 200] [--up 05:40-22:40] [--site-per-day N]

Read-only. What it starts from (live database, read-only session):
  * every approved email draft Send could take, with its niche, country and
    first-touch / follow-up kind;
  * every lead whose next follow-up falls due inside the window (the shipped
    Find Due rules: 6 days after the later of its last send and its last
    follow-up drafted, at most two, CRO and research_site alike);
  * research-site first touches still to come: --site-per-day new approved
    drafts a day (what the pipeline drafts once enrichment has worked through
    the ingested sites; 0 = only what is approved now);
  * the mailbox's real recent sends (warm-up ceiling, 20-minute floor).
Then for each run: a tick every 10 minutes while the host is up (--up, UTC;
the measured habit is 05:40-22:40), Decide Send's own code with its own
pacing (40 % coin, 20-minute floor, 50 a day, the recipient's 09:00-17:00 and
weekend rule); a send makes the lead's next follow-up due 6 days later.
Reported: the median and the 10th-90th percentile of sends per day and per
niche over the runs. A forecast, not a promise: replies, bounces, opt-outs and
the host's real hours change it.
"""
import argparse
import datetime as dt
import json
import os
import random
import subprocess
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEND = os.path.join(HERE, "..", "workflows", "send.json")

QUEUE_SQL = r"""
SELECT json_build_object(
 'now', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
 'approved', (SELECT coalesce(json_agg(x), '[]') FROM (
    SELECT d.id AS draft_id, d.lead_id, l.country, coalesce(lead_niche(e.raw_extraction, l.source), 'none') AS niche,
           CASE WHEN d.variant LIKE 'follow-up-%' THEN substring(d.variant from 'follow-up-(\d)')::int ELSE 0 END AS n
      FROM drafts d JOIN leads l ON l.id = d.lead_id LEFT JOIN enrichments e ON e.lead_id = l.id
      JOIN contacts c ON c.lead_id = l.id
     WHERE d.channel = 'email' AND d.status = 'approved' AND coalesce(d.variant, '') NOT LIKE 'reply/%'
       AND c.verified) x),
 'due', (SELECT coalesce(json_agg(x), '[]') FROM (
    SELECT l.id AS lead_id, l.country, lead_niche(e.raw_extraction, l.source) AS niche,
           coalesce(fu.created, 0) + 1 AS next_n,
           to_char(greatest(ls.sent_at, coalesce(fu.last_created, ls.sent_at)) + interval '6 days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS due_at
      FROM leads l
      JOIN (SELECT lead_id, max(sent_at) sent_at FROM outreach_log WHERE channel = 'email' AND message_id IS NOT NULL GROUP BY 1) ls
        ON ls.lead_id = l.id
      LEFT JOIN enrichments e ON e.lead_id = l.id
      LEFT JOIN (SELECT lead_id, count(DISTINCT regexp_replace(variant, '[/+].*$', ''))::int created,
                        count(*) FILTER (WHERE status IN ('pending', 'approved'))::int open, max(created_at) last_created
                   FROM drafts WHERE channel = 'email' AND variant LIKE 'follow-up-%' GROUP BY 1) fu ON fu.lead_id = l.id
     WHERE l.status = 'sent' AND coalesce(fu.created, 0) < 2 AND coalesce(fu.open, 0) = 0
       AND lead_niche(e.raw_extraction, l.source) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM outreach_log o WHERE o.lead_id = l.id AND (o.replied OR o.outcome = 'bounced'))) x),
 'recent', (SELECT coalesce(json_agg(to_char(sent_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')), '[]')
              FROM mailbox_sent WHERE external AND sent_at > now() - interval '2 days'),
 'first_external', (SELECT to_char(min(sent_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') FROM mailbox_sent WHERE external),
 'site_countries', (SELECT coalesce(json_agg(country), '[]') FROM leads WHERE source IN ('sites-ctgov', 'sites-overture')));
"""

SIM_JS = r"""
const fs = require('fs');
const a = JSON.parse(fs.readFileSync(0, 'utf8'));
const wf = JSON.parse(fs.readFileSync(a.send, 'utf8'));
const src = wf.nodes.find((n) => n.name === 'Decide Send').parameters.jsCode;
const { decide } = new Function(src.slice(0, src.lastIndexOf('// Node body')) + '\nreturn { decide };')();
const ID = 'Abdullah Amir\n';
function body(niche) {
  const title = niche === 'research_site' ? 'Software Engineer, Amitrix Labs' : 'Founder, Amitrix Labs';
  return 'Hi there,\n\n' + 'word '.repeat(40).trim() + '\n\nCheers!\n' + ID + title;
}
function run(seed) {
  let s = seed; const rng = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
  const start = Date.parse(a.now), end = start + a.days * 86400000;
  let nextId = 10000000;
  const queue = a.approved.map((d) => Object.assign({ ready: start }, d));
  const due = a.due.map((d) => ({ lead_id: d.lead_id, country: d.country, niche: d.niche, n: d.next_n, ready: Date.parse(d.due_at) }));
  let recent = a.recent.slice();
  const perDay = {};
  const siteFeed = a.site_per_day;
  let siteCountryIdx = 0;
  for (let t = start; t < end; t += 600000) {
    const day = new Date(t).toISOString().slice(0, 10);
    perDay[day] = perDay[day] || { cro: 0, research_site: 0, none: 0 };
    // New research-site first touches, spread over the day.
    if (siteFeed > 0 && a.site_countries.length && rng() < siteFeed / 144) {
      queue.push({ draft_id: nextId++, lead_id: nextId, country: a.site_countries[siteCountryIdx++ % a.site_countries.length], niche: 'research_site', n: 0, ready: t });
    }
    for (let i = due.length - 1; i >= 0; i--) {
      if (due[i].ready <= t) { const d = due.splice(i, 1)[0]; queue.push({ draft_id: nextId++, lead_id: d.lead_id, country: d.country, niche: d.niche, n: d.n, ready: t }); }
    }
    const minute = new Date(t).getUTCHours() * 60 + new Date(t).getUTCMinutes();
    if (minute < a.up[0] || minute > a.up[1]) continue;
    const cands = queue.filter((q) => q.ready <= t).map((q) => ({
      draft_id: q.draft_id, lead_id: q.lead_id, channel: 'email', status: 'approved',
      variant: q.n ? 'follow-up-' + q.n + '/template/A' : 'template/A/company/unnamed', subject: 's', body: body(q.niche),
      domain: 'x.example', country: q.country, lead_status: q.n ? 'sent' : 'drafted', to_addr: 'info@x.example',
      verified: true, lead_domain_blocked: false, recipient_domain_blocked: false, prior_sends: q.n ? 1 : 0,
      prior_manual_sends: 0, follow_ups_sent: q.n ? q.n - 1 : 0, replied: false, bounced: false, niche: q.niche,
    }));
    const iso = new Date(t).toISOString();
    const last = recent.filter((x) => Date.parse(x) <= t).sort().pop() || null;
    const d = decide({ clock: iso, min_gap_min: 20, send_probability: 0.4, sent_mirror_synced_at: iso,
      first_external_send_at: a.first_external, recent_sends: recent.map((x) => ({ at: x, kind: 'sent-folder' })),
      last_external_send_at: last, unconfirmed_workflow_sends: [], candidates: cands, pauses: [] }, rng);
    if (d.send && d.payload) {
      const k = queue.findIndex((q) => q.draft_id === d.payload.draft_id);
      const q = queue.splice(k, 1)[0];
      perDay[day][q.niche] = (perDay[day][q.niche] || 0) + 1;
      recent.push(iso);
      recent = recent.filter((x) => Date.parse(x) > t - 2 * 86400000);
      if (q.n < 2) due.push({ lead_id: q.lead_id, country: q.country, niche: q.niche, n: q.n + 1, ready: t + 6 * 86400000 });
    }
  }
  return perDay;
}
const runs = [];
for (let i = 0; i < a.runs; i++) runs.push(run(1000 + i * 7919));
process.stdout.write(JSON.stringify(runs));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--up", default="05:40-22:40")
    ap.add_argument("--site-per-day", type=float, default=0)
    ap.add_argument("--db", default="novascout")
    a = ap.parse_args()
    res = subprocess.run(["docker", "exec", "-i", "nova-scout-postgres-1", "psql", "-U", "novascout", "-d", a.db, "-At",
                          "-v", "ON_ERROR_STOP=1", "-c", "SET default_transaction_read_only = on;", "-c", QUEUE_SQL],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        sys.exit(res.stderr)
    data = json.loads(res.stdout[res.stdout.index("{"):])
    lo, hi = [int(x[:2]) * 60 + int(x[3:]) for x in a.up.split("-")]
    data.update({"send": SEND, "days": a.days, "runs": a.runs, "up": [lo, hi], "site_per_day": a.site_per_day})
    out = subprocess.run(["node", "-e", SIM_JS], input=json.dumps(data), capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        sys.exit(out.stderr)
    runs = json.loads(out.stdout)
    print("start %s; approved now: %d (%s); follow-ups due in the window: %d; new site first touches/day: %g; host up %s UTC"
          % (data["now"], len(data["approved"]),
             ", ".join("%s %d" % (k, sum(1 for x in data["approved"] if x["niche"] == k)) for k in ("cro", "research_site")),
             len(data["due"]), a.site_per_day, a.up))
    print("%-11s %8s %8s %8s %12s" % ("day (UTC)", "CRO", "sites", "total", "total p10-p90"))
    tot = []
    for day in sorted(runs[0]):
        c = [r[day].get("cro", 0) for r in runs]
        s = [r[day].get("research_site", 0) for r in runs]
        t = sorted(x + y for x, y in zip(c, s))
        tot.append(statistics.median(t))
        print("%-11s %8g %8g %8g %6d-%-5d" % (day, statistics.median(c), statistics.median(s), statistics.median(t),
                                              t[len(t) // 10], t[(len(t) * 9) // 10]))
    print("14-day median total: %g" % sum(tot))


if __name__ == "__main__":
    main()
