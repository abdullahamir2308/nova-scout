"""Build the Nova Scout dashboard (Grafana) from the metric views of migration 033.

    python dashboard/build_dashboard.py

Writes dashboard/grafana/dashboards/nova-scout.json, which the grafana service loads read-only
(dashboard/grafana/provisioning/dashboards/novascout.yaml). Generated, not hand-edited: change a panel here,
rebuild, commit both. Grafana re-reads the file within a minute; nothing to import.

Refuses to build when:
  * a panel's SQL reads anything but a `metrics.` view that migration 033 creates (the dashboard's role can
    read nothing else, so such a panel would only ever show an error);
  * the migration's constants disagree with the Master Ref: the safety brake's window and share (Section 9,
    Workflow 6's brake sentence -- the same parse as n8n/sendtrack/build_workflow.py's load_brake), the A/B
    sample per arm (Section 9, "Dashboard"), the warm-up table (Section 5), the sender's zone
    (docker-compose's GENERIC_TIMEZONE);
  * the migration's arm expression is not the Daily Digest's.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
MASTER_REF = os.path.join(ROOT, "NovaScout_MasterRef.md")
MIGRATION = os.path.join(ROOT, "postgres", "migrations", "033_dashboard_metrics.sql")
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
SENDTRACK_BUILD = os.path.join(ROOT, "n8n", "sendtrack", "build_workflow.py")
OUT = os.path.join(HERE, "grafana", "dashboards", "nova-scout.json")

DS = {"type": "grafana-postgresql-datasource", "uid": "novascout-metrics"}
NICHE = "niche IN (${niche:sqlstring})"
# The arm as the Daily Digest reads it (Load Digest's niche_totals), byte for byte.
ARM_EXPR = "substring(d.variant from '(?:^|/)template/([AB])')"


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------------------------------------
# What the doc and the migration say
# ---------------------------------------------------------------------------------------------------------
def load_brake(doc):
    m = re.search(r"if bounces over the last (\w+) days exceed (\d+(?:\.\d+)?)% of sends, or any spam complaint "
                  r"arrives, Send pauses and the operator is alerted", doc)
    if not m:
        raise AssertionError("the Send safety brake sentence not found in Section 9 of the Master Ref")
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
    days = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
    pct = float(m.group(2))
    return days, (int(pct) if pct == int(pct) else pct)


def load_ab_sample(doc):
    m = re.search(r"an arm is compared only once it has (\d+) first touches", doc)
    if not m:
        raise AssertionError("the A/B sample sentence not found in Section 9 ('Dashboard') of the Master Ref -- "
                             "expected 'an arm is compared only once it has N first touches'")
    return int(m.group(1))


def load_warmup(doc):
    rows = re.findall(r"^\| Week (\d+)(\+?) \| (\d+)", doc, re.M)
    if not rows:
        raise AssertionError("the warm-up table not found in Section 5 of the Master Ref")
    return [(int(w), int(n)) for w, _, n in rows]


def load_zone(compose):
    m = re.search(r"GENERIC_TIMEZONE:\s*(\S+)", compose)
    if not m:
        raise AssertionError("GENERIC_TIMEZONE not found in docker-compose.yml")
    return m.group(1)


def migration_constants(sql):
    m = re.search(r"CREATE VIEW metrics_private\.constants AS\s*SELECT(.+?);", sql, re.S)
    if not m:
        raise AssertionError("metrics_private.constants not found in %s" % MIGRATION)
    out = {}
    for value, name in re.findall(r"('[^']*'(?:::text)?|\d+)\s+AS\s+(\w+)", m.group(1)):
        out[name] = value.split("::")[0].strip("'") if value.startswith("'") else int(value)
    return out


def migration_warmup(sql):
    m = re.search(r"CREATE VIEW metrics_private\.warmup \(week_from, per_day\) AS\s*VALUES ([^;]+);", sql)
    if not m:
        raise AssertionError("metrics_private.warmup not found in %s" % MIGRATION)
    return [(int(a), int(b)) for a, b in re.findall(r"\((\d+),\s*(\d+)\)", m.group(1))]


def migration_views(sql):
    return set(re.findall(r"CREATE VIEW metrics\.(\w+)", sql))


def grafana_service(compose):
    m = re.search(r"^  grafana:\n(.*?)(?=^  \S|^\S)", compose, re.S | re.M)
    if not m:
        raise AssertionError("no grafana service in docker-compose.yml")
    return m.group(1)


def check_compose(compose):
    """Localhost only, pinned like every other image (Master Ref Section 3), read-only provisioning."""
    svc = grafana_service(compose)
    if not re.search(r"^\s+image: grafana/grafana:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}\s*$", svc, re.M):
        raise AssertionError("the grafana image is not pinned to version@sha256:<digest> (Master Ref Section 3)")
    ports = re.findall(r"^\s+- \"([^\"]+)\"\s*$", svc.split("ports:", 1)[1], re.M) if "ports:" in svc else []
    if ports != ["127.0.0.1:3000:3000"]:
        raise AssertionError("grafana must publish exactly 127.0.0.1:3000:3000 (this PC only), not %r" % ports)
    for mount in ("./dashboard/grafana/provisioning:/etc/grafana/provisioning:ro",
                  "./dashboard/grafana/dashboards:/etc/grafana/dashboards:ro"):
        if mount not in svc:
            raise AssertionError("grafana must mount %s" % mount)
    for env in ('GF_AUTH_ANONYMOUS_ENABLED: "false"', 'GF_USERS_ALLOW_SIGN_UP: "false"',
                'GF_UNIFIED_ALERTING_ENABLED: "false"'):
        if env not in svc:
            raise AssertionError("grafana must set %s" % env)
    if "POSTGRES_PASSWORD" in svc or "POSTGRES_USER" in svc:
        raise AssertionError("grafana must not be given the superuser's credentials")


def check_sources(doc, sql, compose, sendtrack):
    """Every number the views judge by is the system's own. Raises on the first disagreement."""
    consts = migration_constants(sql)
    days, pct = load_brake(doc)
    if (consts.get("brake_days"), consts.get("brake_pct")) != (days, pct):
        raise AssertionError("033's brake is %s days / %s%%, the Master Ref's is %s days / %s%%"
                             % (consts.get("brake_days"), consts.get("brake_pct"), days, pct))
    sample = load_ab_sample(doc)
    if consts.get("ab_sample_per_arm") != sample:
        raise AssertionError("033's A/B sample is %s per arm, the Master Ref's is %s"
                             % (consts.get("ab_sample_per_arm"), sample))
    warm = load_warmup(doc)
    if migration_warmup(sql) != warm:
        raise AssertionError("033's warm-up table %r is not Section 5's %r" % (migration_warmup(sql), warm))
    zone = load_zone(compose)
    if consts.get("sender_zone") != zone:
        raise AssertionError("033 counts days in %r, docker-compose's GENERIC_TIMEZONE is %r"
                             % (consts.get("sender_zone"), zone))
    if ARM_EXPR not in sendtrack:
        raise AssertionError("the Daily Digest no longer reads the arm as %s -- update ARM_EXPR and 033 together"
                             % ARM_EXPR)
    for found in re.findall(r"substring\(d\.variant from '[^']*'\)", sql):
        if found != ARM_EXPR:
            raise AssertionError("033 reads the arm as %s, the Daily Digest as %s" % (found, ARM_EXPR))
    check_compose(compose)
    # The brake's numbers come from send_brake_stats() itself, not a copy of it.
    if "send_brake_stats(now(), c.brake_days)" not in sql:
        raise AssertionError("033's brake_now() no longer calls send_brake_stats() -- the dashboard's bounce rate "
                             "must be the brake's own number")
    return consts


# ---------------------------------------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------------------------------------
_ids = [0]


def _id():
    _ids[0] += 1
    return _ids[0]


def target(sql, fmt="table", ref="A"):
    return {"refId": ref, "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": sql}


def row(title, y):
    return {"type": "row", "id": _id(), "title": title, "collapsed": False, "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}


def stat(title, desc, sql, x, y, w, h=6, thresholds=None, mappings=None, unit=None, color_mode="value",
         fields="", text_mode="value_and_name", threshold_field=None):
    """thresholds colour every value, or only threshold_field's when one is named."""
    steps = thresholds or [{"color": "text", "value": None}]
    overrides = []
    if threshold_field:
        overrides = [{"matcher": {"id": "byName", "options": threshold_field},
                      "properties": [{"id": "thresholds", "value": {"mode": "absolute", "steps": steps}}]}]
        steps = [{"color": "text", "value": None}]
    return {
        "type": "stat", "id": _id(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(sql)],
        "fieldConfig": {"defaults": {
            "unit": unit or "none", "decimals": None,
            "color": {"mode": "thresholds"},
            "mappings": mappings or [],
            "thresholds": {"mode": "absolute", "steps": steps},
        }, "overrides": overrides},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": fields, "values": False},
                    "orientation": "horizontal", "textMode": text_mode, "colorMode": color_mode,
                    "graphMode": "none", "justifyMode": "auto", "wideLayout": True,
                    "showPercentChange": False},
    }


def table(title, desc, sql, x, y, w, h, footer_sum=False, overrides=None):
    opts = {"showHeader": True, "cellHeight": "sm"}
    opts["footer"] = {"show": bool(footer_sum), "reducer": ["sum"], "fields": "", "countRows": False}
    return {
        "type": "table", "id": _id(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(sql)],
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": True},
                                     "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}},
                        "overrides": overrides or []},
        "options": opts,
    }


def bars(title, desc, sqls, x, y, w, h, cap_series):
    return {
        "type": "timeseries", "id": _id(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "timeFrom": "14d",
        "targets": [target(s, fmt="time_series", ref=chr(65 + i)) for i, s in enumerate(sqls)],
        "fieldConfig": {
            "defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1, "barAlignment": 0,
                                    "stacking": {"mode": "normal", "group": "A"}, "showPoints": "never",
                                    "axisSoftMin": 0},
                         "unit": "none", "decimals": 0, "color": {"mode": "palette-classic"}},
            "overrides": [{"matcher": {"id": "byName", "options": cap_series},
                           "properties": [{"id": "custom.drawStyle", "value": "line"},
                                          {"id": "custom.stacking", "value": {"mode": "none", "group": "A"}},
                                          {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                                          {"id": "custom.fillOpacity", "value": 0},
                                          {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "none"}},
    }


def panels(consts):
    brake_pct, brake_days, sample = consts["brake_pct"], consts["brake_days"], consts["ab_sample_per_arm"]
    zone = consts["sender_zone"]
    p = []
    y = 0

    # 1. Today ------------------------------------------------------------------------------------------
    p.append(row("Today", y)); y += 1
    p.append(stat(
        "Sent today / daily cap",
        "Everything that counts toward the warm-up ceiling today, in the sender's day (%s): every external "
        "message the Sent mirror holds (the workflow's, replies, manual sends) plus open claims -- Load Send "
        "State's own count. The cap is the Section 5 warm-up ceiling for today. The cap is the mailbox's; "
        "with a niche filter, 'sent today' is that niche's share." % zone,
        "SELECT sum(counted_toward_cap) AS \"sent today\", max(daily_cap) AS \"daily cap\",\n"
        "       max(daily_cap) - sum(counted_toward_cap) AS \"left today\"\n"
        "  FROM metrics.sends_daily\n WHERE is_today AND " + NICHE,
        0, y, 6))
    p.append(bars(
        "Sends per day, last 14 days",
        "Per sender-day (%s) and niche: what counted toward the warm-up ceiling. Dashed: the daily cap." % zone,
        ["SELECT (sender_day::timestamp AT TIME ZONE '%s') AS time, niche AS metric, counted_toward_cap AS value\n"
         "  FROM metrics.sends_daily\n WHERE %s\n ORDER BY 1, 2" % (zone, NICHE),
         "SELECT (sender_day::timestamp AT TIME ZONE '%s') AS time, max(daily_cap) AS \"daily cap\"\n"
         "  FROM metrics.sends_daily\n GROUP BY 1\n ORDER BY 1" % zone],
        6, y, 12, 6, "daily cap"))
    p.append(stat(
        "Approved queue left",
        "Approved email drafts Send can still take (Load Send State's filter: approved, email, not a reply, "
        "source not held), divided by the cold sends of the last %d days per day. Blank when nothing was sent "
        "in that window." % consts["runway_days"],
        "SELECT sum(approved_queue) AS \"approved emails\",\n"
        "       round(sum(cold_sends_in_window)::numeric / max(rate_window_days), 1) AS \"sends / day (last 7 days)\",\n"
        "       round(sum(approved_queue) / nullif(sum(cold_sends_in_window)::numeric / max(rate_window_days), 0), 1)"
        " AS \"days left\"\n  FROM metrics.queue_runway\n WHERE " + NICHE,
        18, y, 6))
    y += 6

    # 2. Deliverability ---------------------------------------------------------------------------------
    p.append(row("Deliverability", y)); y += 1
    p.append(stat(
        "Permanent-bounce rate, last %d days" % brake_days,
        "The safety brake's own number, for the whole mailbox (send_brake_stats(), which Check Brake trips on): "
        "permanent bounces among confirmed sends since the later of %d days ago and the last cleared "
        "bounce-rate pause. The brake trips strictly over %s%%. Not filtered by niche."
        % (brake_days, brake_pct),
        "SELECT max(brake_rate_pct) AS \"bounce rate %\", max(brake_bounced) AS \"permanent bounces\",\n"
        "       max(brake_sends) AS \"sends\", max(brake_limit_pct) AS \"brake at %\"\n"
        "  FROM metrics.deliverability",
        0, y, 8, h=6,
        thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": brake_pct * 0.7},
                    {"color": "red", "value": brake_pct}], threshold_field="bounce rate %"))
    p.append(stat(
        "Send brake",
        "An open send_pauses row stops Send until the operator clears it (Master Ref Section 9, Workflow 6). "
        "Mailbox-wide.",
        "SELECT CASE WHEN max(open_pauses) = 0 THEN 'Sending: no pause'\n"
        "            ELSE 'PAUSED: ' || max(open_pause_reasons) END AS \"send brake\"\n"
        "  FROM metrics.deliverability",
        8, y, 8, h=6, color_mode="background", fields="/^send brake$/", text_mode="value",
        mappings=[{"type": "regex", "options": {"pattern": "^PAUSED.*", "result": {"color": "red", "index": 0}}},
                  {"type": "regex", "options": {"pattern": "^Sending.*", "result": {"color": "green", "index": 1}}}]))
    p.append(stat(
        "Spam complaints",
        "Counted by the brake: complaints since the last cleared spam-complaint pause, mailbox-wide (any one "
        "pauses Send). Ever: every inbound message flagged spam_complaint, in the selected niches.",
        "SELECT max(brake_complaints) AS \"counted by the brake\", sum(spam_complaints_ever) AS \"ever\"\n"
        "  FROM metrics.deliverability\n WHERE " + NICHE,
        16, y, 8, h=6,
        thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
        threshold_field="counted by the brake"))
    y += 6

    # 3. Replies ----------------------------------------------------------------------------------------
    p.append(row("Replies", y)); y += 1
    p.append(stat(
        "Replies and positive replies",
        "Replies: inbound messages Mailbox Watch classified `reply`. Positive: replies the operator tagged "
        "positive or meeting booked in NocoDB (inbound_messages.reply_outcome, migration 032). Not tagged: "
        "replies with no tag yet.",
        "SELECT sum(total) AS \"replies\", sum(tagged_positive + tagged_meeting_booked) AS \"positive\",\n"
        "       sum(tagged_meeting_booked) AS \"meetings booked\", sum(untagged) AS \"not tagged yet\"\n"
        "  FROM metrics.replies_by_type\n WHERE type = 'reply' AND " + NICHE,
        0, y, 8, h=8))
    p.append(table(
        "What came back, by type",
        "inbound_messages by Mailbox Watch's classification. out-of-office = the auto-reply class (it also "
        "holds DMARC reports and other machine mail). unmatched = mail tied to no lead (always niche none).",
        "SELECT type, sum(last_7_days) AS \"last 7 days\", sum(total) AS \"all time\",\n"
        "       sum(bounce_permanent) AS \"permanent\", sum(bounce_transient) AS \"delay\"\n"
        "  FROM metrics.replies_by_type\n WHERE " + NICHE + "\n GROUP BY type, type_order\n ORDER BY type_order",
        8, y, 16, 8))
    y += 8
    p.append(table(
        "Latest replies",
        "The 20 newest replies and opt-outs matched to a lead. Outcome is the operator's tag in NocoDB "
        "(inbound_messages.reply_outcome, grid 'Replies — set outcome'); set it there. Arm pre-A/B: the lead's "
        "first email predates the template arms (2026-10-19).",
        "SELECT received_at AS \"received\", company_name AS \"company\", country, niche,\n"
        "       coalesce(arm, 'pre-A/B') AS arm, type,\n"
        "       coalesce(outcome, 'not tagged') AS \"outcome\", subject, excerpt\n"
        "  FROM metrics.latest_replies\n WHERE " + NICHE + "\n ORDER BY received_at DESC\n LIMIT 20",
        0, y, 24, 7))
    y += 7

    # 4. A/B per niche ----------------------------------------------------------------------------------
    p.append(row("A/B per niche", y)); y += 1
    p.append(table(
        "Arm A against arm B",
        "Template emails sent, by niche and arm (the arm read from drafts.variant exactly as the Daily Digest "
        "reads it). Replies and positive are leads. No winner is named: an arm is compared only once it has "
        "%d first touches (Master Ref Section 9, Dashboard)." % sample,
        "SELECT niche, arm, sample_progress AS \"first touches\", follow_ups AS \"follow-ups\",\n"
        "       replied_leads AS \"replies\", positive_leads AS \"positive\", reply_rate_pct AS \"reply rate %\",\n"
        "       status\n  FROM metrics.ab_arms\n WHERE " + NICHE + " AND (niche <> 'none' OR first_touches > 0)\n"
        " ORDER BY niche, arm",
        0, y, 24, 6))
    y += 6

    # 5. Funnel and queue -------------------------------------------------------------------------------
    p.append(row("Funnel and queue", y)); y += 1
    p.append(table(
        "Funnel by niche and source",
        "A lead counts at a stage when that stage's record exists: enriched = an enrichments row; scored = a "
        "scores row (disqualified among them); contact found = a contact with an email address; drafted = an "
        "email first touch or follow-up; approved = one of them approved (or sent); sent = a confirmed email; "
        "replied = a send marked replied.",
        "SELECT niche, source, leads, enriched, scored, disqualified, contact_found AS \"contact found\",\n"
        "       drafted, approved, sent, replied\n  FROM metrics.funnel\n WHERE " + NICHE + "\n ORDER BY niche, source",
        0, y, 24, 9, footer_sum=True))
    y += 9
    p.append(table(
        "Held drafts by reason",
        "Every pending draft, counted by hold_reason_class() exactly as the Daily Digest's HELD block counts "
        "them; 'needs a person' is the digest's NEEDS YOU rule.",
        "SELECT hold_class AS \"hold reason\", sum(held) AS \"held\", sum(needs_person) AS \"needs a person\"\n"
        "  FROM metrics.held_by_reason\n WHERE " + NICHE + "\n GROUP BY hold_class\n ORDER BY 2 DESC, 1",
        0, y, 12, 8, footer_sum=True))
    y += 8
    return p


def dashboard(consts):
    _ids[0] = 0
    return {
        "uid": "nova-scout-v1",
        "title": "Nova Scout",
        "description": "Is Nova Scout working? Read-only, from the metrics views (migration 033). Generated by "
                       "dashboard/build_dashboard.py -- edit there, not here.",
        "tags": ["nova-scout"],
        "editable": False,
        "graphTooltip": 0,
        "timezone": "browser",
        "refresh": "1m",
        "time": {"from": "now-14d", "to": "now"},
        "timepicker": {"refresh_intervals": ["1m", "5m", "15m"]},
        "schemaVersion": 41,
        "version": 1,
        "links": [],
        "annotations": {"list": []},
        "templating": {"list": [{
            "name": "niche", "label": "Niche", "type": "query", "datasource": DS,
            "query": "SELECT niche FROM metrics.niche_list ORDER BY niche",
            "definition": "SELECT niche FROM metrics.niche_list ORDER BY niche",
            "multi": True, "includeAll": True, "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "refresh": 1, "sort": 1, "hide": 0, "options": [],
        }]},
        "panels": panels(consts),
    }


def all_sql(dash):
    out = [dash["templating"]["list"][0]["query"]]
    for p in dash["panels"]:
        for t in p.get("targets", []):
            out.append(t["rawSql"])
    return out


def check_reads(dash, views):
    """Every relation a panel reads is a metrics view the migration creates."""
    for sql in all_sql(dash):
        rels = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", sql)
        if not rels:
            raise AssertionError("a panel query reads no relation: %r" % sql)
        for rel in rels:
            schema, _, name = rel.partition(".")
            if schema != "metrics" or name not in views:
                raise AssertionError("a panel reads %r -- only migration 033's metrics views are readable "
                                     "(%s)" % (rel, ", ".join(sorted(views))))


def build():
    doc, sql = read(MASTER_REF), read(MIGRATION)
    consts = check_sources(doc, sql, read(COMPOSE), read(SENDTRACK_BUILD))
    dash = dashboard(consts)
    check_reads(dash, migration_views(sql))
    return dash, consts


def render(dash):
    return json.dumps(dash, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    dash, consts = build()
    text = render(dash)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("wrote %s: %d panels, %d queries, all on metrics views" %
          (os.path.relpath(OUT, ROOT), sum(1 for p in dash["panels"] if p["type"] != "row"), len(all_sql(dash))))
    print("  brake %d days / %s%%, A/B sample %d per arm, sender day %s" %
          (consts["brake_days"], consts["brake_pct"], consts["ab_sample_per_arm"], consts["sender_zone"]))
    sys.exit(0)
