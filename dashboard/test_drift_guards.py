"""Drift guards for the dashboard (Master Ref Section 9, "Dashboard").

    python dashboard/test_drift_guards.py

The committed dashboard must be the build's output; the build must refuse when any number it judges by
stops being the system's own (each guard is broken once below and must fire); the data source must be the
read-only role; the reply-outcome values must be the doc's. No database needed, except the last test, which
reads the live schema when the stack is up and is skipped otherwise.
"""
import io
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_dashboard as B  # noqa: E402

DOC = B.read(B.MASTER_REF)
SQL = B.read(B.MIGRATION)
COMPOSE = B.read(B.COMPOSE)
SENDTRACK = B.read(B.SENDTRACK_BUILD)
REPLY_MIGRATION = B.read(os.path.join(B.ROOT, "postgres", "migrations", "032_reply_outcome.sql"))
DATASOURCE = B.read(os.path.join(HERE, "grafana", "provisioning", "datasources", "novascout.yaml"))
ENV_EXAMPLE = B.read(os.path.join(B.ROOT, ".env.example"))


def once(text, old, new):
    assert text.count(old) == 1, "mutation anchor not unique or gone: %r" % old
    return text.replace(old, new)


class Committed(unittest.TestCase):
    def test_json_is_the_build(self):
        dash, _ = B.build()
        self.assertEqual(B.render(dash), B.read(B.OUT),
                         "grafana/dashboards/nova-scout.json is stale -- run dashboard/build_dashboard.py")

    def test_every_panel_filters_on_the_niche(self):
        dash, _ = B.build()
        mailbox_wide = {"Permanent-bounce rate, last 3 days", "Send brake"}
        for p in dash["panels"]:
            for t in p.get("targets", []):
                if p["title"] in mailbox_wide or "max(daily_cap)" in t["rawSql"] and "GROUP BY 1" in t["rawSql"]:
                    continue  # the mailbox's own numbers, documented as not filtered
                self.assertIn(B.NICHE, t["rawSql"], "panel %r ignores the niche filter" % p["title"])


class Guards(unittest.TestCase):
    def refuses(self, doc=DOC, sql=SQL, compose=COMPOSE, sendtrack=SENDTRACK):
        with self.assertRaises(AssertionError):
            B.check_sources(doc, sql, compose, sendtrack)

    def test_clean_sources_pass(self):
        B.check_sources(DOC, SQL, COMPOSE, SENDTRACK)

    def test_brake_share_in_doc(self):
        self.refuses(doc=once(DOC, "exceed 10% of sends, or any spam complaint", "exceed 12% of sends, or any spam complaint"))

    def test_brake_days_in_doc(self):
        self.refuses(doc=once(DOC, "if bounces over the last 3 days exceed", "if bounces over the last 5 days exceed"))

    def test_brake_in_migration(self):
        self.refuses(sql=once(SQL, "10                   AS brake_pct", "12                   AS brake_pct"))

    def test_ab_sample_in_doc(self):
        self.refuses(doc=once(DOC, "an arm is compared only once it has 100 first touches",
                              "an arm is compared only once it has 200 first touches"))

    def test_ab_sample_sentence_gone(self):
        self.refuses(doc=once(DOC, "an arm is compared only once it has 100 first touches", "an arm is compared later"))

    def test_warmup_in_doc(self):
        self.refuses(doc=once(DOC, "| Week 5+ | 50 (hard ceiling) |", "| Week 5+ | 60 (hard ceiling) |"))

    def test_warmup_in_migration(self):
        self.refuses(sql=once(SQL, "(4, 20), (5, 50)", "(4, 20), (5, 40)"))

    def test_zone(self):
        self.refuses(compose=once(COMPOSE, "GENERIC_TIMEZONE: Asia/Karachi", "GENERIC_TIMEZONE: Asia/Dubai"))

    def test_digest_arm_changed(self):
        self.refuses(sendtrack=SENDTRACK.replace(B.ARM_EXPR, "substring(d.variant from 'arm/([AB])')"))

    def test_migration_arm_changed(self):
        self.refuses(sql=SQL.replace(B.ARM_EXPR, "substring(d.variant from 'template/([AB])')", 1))

    def test_brake_not_from_send_brake_stats(self):
        self.refuses(sql=once(SQL, "send_brake_stats(now(), c.brake_days)", "jsonb_build_object()"))

    def test_grafana_published_beyond_localhost(self):
        self.refuses(compose=once(COMPOSE, '"127.0.0.1:3000:3000"', '"3000:3000"'))

    def test_grafana_unpinned(self):
        self.refuses(compose=re.sub(r"grafana/grafana:13\.2\.5@sha256:[0-9a-f]{64}", "grafana/grafana:latest", COMPOSE))

    def test_grafana_anonymous(self):
        self.refuses(compose=once(COMPOSE, 'GF_AUTH_ANONYMOUS_ENABLED: "false"', 'GF_AUTH_ANONYMOUS_ENABLED: "true"'))

    def test_grafana_given_superuser(self):
        self.refuses(compose=once(COMPOSE, "      GF_USERS_ALLOW_SIGN_UP:",
                                  "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}\n      GF_USERS_ALLOW_SIGN_UP:"))

    def test_panel_reading_a_table(self):
        dash, _ = B.build()
        dash["panels"][1]["targets"][0]["rawSql"] = "SELECT count(*) FROM public.leads"
        with self.assertRaises(AssertionError):
            B.check_reads(dash, B.migration_views(SQL))

    def test_panel_reading_a_private_object(self):
        dash, _ = B.build()
        dash["panels"][1]["targets"][0]["rawSql"] = "SELECT * FROM metrics_private.constants"
        with self.assertRaises(AssertionError):
            B.check_reads(dash, B.migration_views(SQL))

    def test_panel_reading_a_view_033_does_not_create(self):
        dash, _ = B.build()
        dash["panels"][1]["targets"][0]["rawSql"] = "SELECT * FROM metrics.nothing_here"
        with self.assertRaises(AssertionError):
            B.check_reads(dash, B.migration_views(SQL))


class Wiring(unittest.TestCase):
    def test_reply_outcome_values_are_the_docs(self):
        doc = re.search(r"reply_outcome \(([^)]+)\)", DOC).group(1).split("|")
        enum = re.findall(r"'([^']+)'", re.search(r"CREATE TYPE reply_outcome AS ENUM \(([^)]+)\)", REPLY_MIGRATION).group(1))
        self.assertEqual(doc, enum)
        self.assertEqual(enum, ["positive", "neutral", "negative", "meeting booked"])

    def test_datasource_is_the_read_only_role(self):
        self.assertIn("user: nova_dashboard", DATASOURCE)
        self.assertIn("password: $NOVASCOUT_DASHBOARD_DB_PASSWORD", DATASOURCE)
        self.assertIn("editable: false", DATASOURCE)
        self.assertIn("database: novascout", DATASOURCE)
        self.assertNotIn("novascout\n", DATASOURCE.split("user:")[1].split("\n")[0] + "\n")

    def test_migration_holds_no_password(self):
        self.assertNotRegex(SQL, r"(?i)\bPASSWORD\s+'")

    def test_env_example_documents_both_secrets_empty(self):
        for key in ("NOVASCOUT_DASHBOARD_DB_PASSWORD", "NOVASCOUT_GRAFANA_ADMIN_PASSWORD"):
            self.assertRegex(ENV_EXAMPLE, r"(?m)^%s=$" % key)

    def test_every_view_carries_the_niche_live(self):
        r = subprocess.run(["docker", "exec", "nova-scout-postgres-1", "psql", "-U", "novascout", "-d", "novascout",
                            "-Atc", "SELECT table_name, bool_or(column_name = 'niche') FROM information_schema.columns "
                            "WHERE table_schema = 'metrics' GROUP BY 1 ORDER BY 1"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            self.skipTest("stack not running")
        rows = dict(ln.split("|") for ln in r.stdout.split())
        self.assertEqual(set(rows), B.migration_views(SQL))
        self.assertEqual([v for v, has in rows.items() if has != "t"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
