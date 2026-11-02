"""Drift guards for the dashboard (Master Ref Section 9, "Dashboard").

    python dashboard/test_drift_guards.py

The committed dashboard must be the build's output; the build must refuse when any number it judges by
stops being the system's own (each guard is broken once below and must fire); the data source must be the
read-only role; the reply-outcome values must be the doc's. For the custom page (v1.1): the committed
web/index.html must be its build's output, its CSS the design reference's byte for byte, its script free of any
external address, and the build must refuse a `dashboard` service or a server statement that steps outside
the read-only posture. No database needed, except the last test, which reads the live schema when the stack
is up and is skipped otherwise.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "web"))
import build_dashboard as B  # noqa: E402
import build_page as P  # noqa: E402

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

    def test_postgres_published_beyond_localhost(self):
        self.refuses(compose=once(COMPOSE, '"127.0.0.1:5432:5432"', '"5432:5432"'))

    def test_n8n_published_on_all_interfaces(self):
        self.refuses(compose=once(COMPOSE, '"127.0.0.1:5678:5678"', '"0.0.0.0:5678:5678"'))

    def test_nocodb_published_unquoted(self):
        self.refuses(compose=once(COMPOSE, '"127.0.0.1:8080:8080"', '8080:8080'))

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


def dash(old, new):
    """The compose file with one change inside the `dashboard` service (the last service before `volumes:`)."""
    head, sep, tail = COMPOSE.partition("\n  dashboard:\n")
    assert sep and tail.count(old) >= 1, "mutation anchor gone from the dashboard service: %r" % old
    return head + sep + tail.replace(old, new, 1)


class PageService(unittest.TestCase):
    """The custom page's compose service and server: this PC only, read-only, nothing but the reader's password."""

    def refuses(self, compose):
        with self.assertRaises(AssertionError):
            B.check_compose(compose)

    def test_clean_service_passes(self):
        B.check_compose(COMPOSE)

    def test_no_dashboard_service(self):
        self.refuses(COMPOSE.partition("\n  dashboard:\n")[0] + "\n\nvolumes:\n  postgres_data:\n")

    def test_published_beyond_localhost(self):
        for bad in ('"3001:3001"', '"0.0.0.0:3001:3001"', '"[::]:3001:3001"'):
            self.refuses(dash('"127.0.0.1:3001:3001"', bad))

    def test_published_on_another_port_than_the_servers(self):
        self.refuses(dash('"127.0.0.1:3001:3001"', '"127.0.0.1:3002:3001"'))
        self.refuses(dash('"127.0.0.1:3001:3001"', '"127.0.0.1:3002:3002"'))

    def test_a_second_published_port(self):
        self.refuses(dash('      - "127.0.0.1:3001:3001"', '      - "127.0.0.1:3001:3001"\n      - "127.0.0.1:3009:3009"'))

    def test_unpinned_or_another_image(self):
        self.refuses(dash("python:3.12.15-alpine3.24@sha256:", "python:3.12.15-alpine3.24@sha256:0000"))
        self.refuses(re.sub(r"(  dashboard:\n.*?image: )python:\S+", r"\1python:latest", COMPOSE, flags=re.S))
        self.refuses(re.sub(r"(  dashboard:\n.*?image: )python:\S+", r"\1alpine:3.20", COMPOSE, flags=re.S))

    def test_not_read_only_or_not_nobody_or_no_memory_cap(self):
        self.refuses(dash("    read_only: true\n", ""))
        self.refuses(dash('    user: "65534:65534"\n', ""))
        self.refuses(dash("    mem_limit: 128m\n", ""))

    def test_code_mounted_writable(self):
        self.refuses(dash("./dashboard/web:/opt/novascout/dashboard:ro", "./dashboard/web:/opt/novascout/dashboard"))

    def test_given_another_credential(self):
        for extra in ("POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}", "POSTGRES_USER: ${POSTGRES_USER}",
                      "NOVASCOUT_MAILBOX_PASSWORD: ${NOVASCOUT_MAILBOX_PASSWORD}", "N8N_ENCRYPTION_KEY: ${N8N_ENCRYPTION_KEY}",
                      "NOVASCOUT_GRAFANA_ADMIN_PASSWORD: ${NOVASCOUT_GRAFANA_ADMIN_PASSWORD}"):
            self.refuses(dash('      PYTHONDONTWRITEBYTECODE: "1"\n', '      PYTHONDONTWRITEBYTECODE: "1"\n      %s\n' % extra))

    def test_not_given_the_readers_password(self):
        self.refuses(dash("      NOVASCOUT_DASHBOARD_DB_PASSWORD: ${NOVASCOUT_DASHBOARD_DB_PASSWORD}\n", ""))

    def test_the_doc_names_every_published_port(self):
        self.assertIn("127.0.0.1:3001", DOC)
        with self.assertRaises(AssertionError):
            B.check_doc_ports(once(DOC, "and since 2026-11-03 the dashboard page `127.0.0.1:3001`",
                                   "and since 2026-11-03 the dashboard page `127.0.0.1:3002`"), COMPOSE)
        with self.assertRaises(AssertionError):
            B.check_doc_ports(DOC.replace("**Published ports (2026-11-01).**", "**Ports.**"), COMPOSE)

    def test_server_reads_as_the_read_only_role(self):
        real = B.read
        text = real(B.PAGE_SERVER)
        self.assertIn('DB_USER = "nova_dashboard"', text)
        with mock.patch.object(B, "read", lambda p: text.replace('DB_USER = "nova_dashboard"', 'DB_USER = "novascout"')
                               if p == B.PAGE_SERVER else real(p)):
            self.refuses(COMPOSE)

    def test_server_port_changed_without_the_compose_file(self):
        real = B.read
        text = real(B.PAGE_SERVER)
        with mock.patch.object(B, "read", lambda p: once(text, "PORT = 3001", "PORT = 3002") if p == B.PAGE_SERVER else real(p)):
            self.refuses(COMPOSE)

    def test_every_server_statement_is_a_select_of_a_metrics_view(self):
        views = B.migration_views(SQL)
        B.check_sql_reads(B.page_sql(), views)
        for bad in ("SELECT count(*) FROM public.leads", "SELECT * FROM metrics_private.constants",
                    "SELECT * FROM metrics.nothing_here", "SELECT 1", "DELETE FROM metrics.funnel",
                    "UPDATE metrics.funnel SET leads = 0", "WITH x AS (SELECT 1) SELECT * FROM x",
                    "SELECT * FROM metrics.funnel f JOIN public.leads l ON true"):
            with self.assertRaises(AssertionError, msg=bad):
                B.check_sql_reads([bad], views)

    def test_the_build_checks_the_servers_statements(self):
        with mock.patch.object(B, "page_sql", lambda: ["SELECT * FROM public.leads"]):
            with self.assertRaises(AssertionError):
                B.build()


class Page(unittest.TestCase):
    """web/index.html: the design reference's look, from the build, with no address outside this origin."""
    REF = P.read(P.REFERENCE)
    HTML = P.read(P.OUT)

    def test_page_is_the_build(self):
        self.assertEqual(P.build(), self.HTML, "web/index.html is stale -- run dashboard/web/build_page.py")

    def test_css_and_fonts_are_the_references_byte_for_byte(self):
        css, _, _ = P.reference_parts(self.REF)
        style = self.HTML.split("<style>\n", 1)[1].split("</style>", 1)[0]
        self.assertGreater(len(css), 100000)                         # the embedded fonts are in it
        self.assertEqual(style, css + P.ADDITIONS_MARK + P.ADDITIONS)

    def test_the_additions_only_add(self):
        # every addition is a new selector; none restates a rule of the reference
        css, _, _ = P.reference_parts(self.REF)
        for selector in re.findall(r"^([^{\n]+)\{", P.ADDITIONS, re.M):
            self.assertNotIn(selector.strip() + "{", css, "an addition restates the reference's rule %s" % selector)

    def test_the_drawing_functions_are_the_references(self):
        _, _, script = P.reference_parts(self.REF)
        for name in ("helpers", "ringSVG", "gaugeSVG", "tick"):
            self.assertIn(P.verbatim(script, name), self.HTML, name)

    def test_the_gauge_bands_are_the_briefs(self):
        # green below 5 %, amber 5 to 10 %, red above 10 % -- the reference's own thresholds at the brake's 10 %
        self.assertIn('b.pct>b.brakeAt?"crit":b.pct>=b.brakeAt/2?"warn":"ok"', self.HTML)

    def test_no_address_outside_this_origin(self):
        for m in re.finditer(r"(?i)(https?:)?//[\w.-]+\.[a-z]{2,}", re.sub(r"data:font/woff2;base64,[A-Za-z0-9+/=]+", "", self.HTML)):
            self.fail("the page names an outside address: %r" % m.group(0))
        self.assertNotRegex(self.HTML, r"(?i)@import|<link[^>]+(stylesheet|preload|prefetch)|<script[^>]+src=|<iframe|<img[^>]+src=\"http")
        self.assertNotIn("url(http", self.HTML)
        self.assertEqual(sorted(set(re.findall(r"url\(([^);]{0,15})", self.HTML))), ["data:font/woff2"])
        self.assertEqual(re.findall(r"fetch\(([^,)]+)", self.HTML), ['"/api/data?niche="+encodeURIComponent(asked'])

    def test_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = re.search(r"<script>\n(.*?)</script>", self.HTML, re.S).group(1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "page.js")
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(script)
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_build_refuses_when_the_reference_changes_under_it(self):
        css, brand, script = P.reference_parts(self.REF)
        real = P.read
        for old, new in (("function ringSVG(", "function ringSVG2("), ("function gaugeSVG()", "function gauge()"),
                         ("function tick()", "function clock()"), ("const fmt =", "const format ="),
                         ("<style>\n", "<style type=\"text/css\">\n"), ('<div class="brand">', '<div class="logo">'),
                         ("<script>\n", "<script defer>\n")):
            self.assertEqual(self.REF.count(old), 1, "mutation anchor not unique: %r" % old)
            with mock.patch.object(P, "read", lambda p, old=old, new=new: real(p).replace(old, new) if p == P.REFERENCE else real(p)):
                with self.assertRaises(AssertionError, msg=old):
                    P.build()

    def test_build_refuses_an_unknown_verbatim_marker(self):
        real = P.read
        with mock.patch.object(P, "read", lambda p: real(p) + "\n/*@verbatim nothing*/" if p == P.APP_JS else real(p)):
            with self.assertRaises(AssertionError):
                P.build()


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
